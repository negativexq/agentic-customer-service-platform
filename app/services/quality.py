"""Quality monitors over recorded conversations (Watchtower-style QA without a model).

Built-in monitors use deterministic signals: frustrated wording, repeated clarification, failed
actions, low ratings and missed first-reply targets. Teams add phrase monitors of their own.
Each flag is one conversation for one monitor; reviewers mark it reviewed, dismissed or coached.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.models.entities import (
    Conversation,
    ConversationMessage,
    Customer,
    QualityFlagReview,
    QualityMonitor,
)
from app.models.workspace import ConversationFeedback
from app.services.studio.procedures import is_clarification, is_failed
from app.ui.insights_schemas import (
    FlagView,
    MonitorPreview,
    MonitorPreviewMatch,
    MonitorView,
    QualityView,
)
from app.ui.schemas import AgentRunView

MAX_MESSAGES = 5000
EXCERPT_CHARS = 220

FRUSTRATION_PHRASES = (
    "not helpful",
    "useless",
    "ridiculous",
    "terrible",
    "frustrat",
    "angry",
    "waste of time",
    "real person",
    "speak to a human",
    "talk to a human",
    "this is wrong",
    "doesn't work",
    "does not work",
)


@dataclass(frozen=True)
class _Hit:
    detail: str
    excerpt: str | None
    at: datetime
    run_id: str | None = None
    intent: str | None = None


@dataclass(frozen=True)
class _BuiltIn:
    key: str
    name: str
    description: str
    severity: str


BUILT_IN = (
    _BuiltIn(
        "frustration",
        "Frustrated customer",
        "The customer used frustrated wording or asked for a real person.",
        "high",
    ),
    _BuiltIn(
        "repeat_clarification",
        "Asked the customer twice",
        "The assistant needed clarification two or more times in one conversation.",
        "medium",
    ),
    _BuiltIn(
        "failed_action",
        "Failed or degraded run",
        "A run failed, a tool errored, or a component was degraded.",
        "high",
    ),
    _BuiltIn("low_csat", "Low rating", "The customer rated the conversation 1 or 2.", "high"),
)


def _excerpt(text: str, phrase: str) -> str:
    lower = text.casefold()
    index = max(lower.find(phrase.casefold()), 0)
    start = max(index - 80, 0)
    snippet = text[start : start + EXCERPT_CHARS]
    return ("…" if start else "") + snippet + ("…" if start + EXCERPT_CHARS < len(text) else "")


def _phrase_hits(
    messages: Sequence[ConversationMessage], phrases: Sequence[str], sender: str | None
) -> dict[int, _Hit]:
    hits: dict[int, _Hit] = {}
    needles = [p.casefold().strip() for p in phrases if p.strip()]
    for message in messages:
        if message.conversation_pk in hits:
            continue
        if sender not in (None, "any") and message.sender_type != sender:
            continue
        text = message.content.casefold()
        for needle in needles:
            if needle in text:
                hits[message.conversation_pk] = _Hit(
                    detail=f"Said “{needle}”",
                    excerpt=_excerpt(message.content, needle),
                    at=message.created_at,
                    run_id=message.agent_run_id,
                )
                break
    return hits


def _run_hits(
    runs: Sequence[AgentRunView],
    pk_by_key: dict[tuple[int, str], int],
    predicate: Callable[[list[AgentRunView]], _Hit | None],
) -> dict[int, _Hit]:
    grouped: dict[int, list[AgentRunView]] = defaultdict(list)
    for run in runs:
        pk = pk_by_key.get((run.customer_id, run.conversation_id))
        if pk is not None:
            grouped[pk].append(run)
    hits: dict[int, _Hit] = {}
    for pk, items in grouped.items():
        hit = predicate(sorted(items, key=lambda run: run.started_at))
        if hit is not None:
            hits[pk] = hit
    return hits


def _repeat_clarification(items: list[AgentRunView]) -> _Hit | None:
    asked = [run for run in items if is_clarification(run)]
    if len(asked) < 2:
        return None
    last = asked[-1]
    return _Hit(f"Asked {len(asked)} times", None, last.started_at, last.run_id, last.intent)


def _failed(items: list[AgentRunView]) -> _Hit | None:
    for run in reversed(items):
        failed_tool = next(
            (t.name for t in run.tools if t.status in {"failed", "error", "timeout"}), None
        )
        if is_failed(run) or failed_tool or run.degraded_components:
            detail = (
                run.failure_category
                or (f"{failed_tool} failed" if failed_tool else None)
                or f"Degraded: {', '.join(run.degraded_components)}"
            )
            return _Hit(detail.replace("_", " "), None, run.started_at, run.run_id, run.intent)
    return None


def _window(
    session: Session, tenant_id: str, since: datetime, customer_ids: set[int] | None
) -> tuple[dict[int, Conversation], list[ConversationMessage]]:
    """Conversations updated in the window and their latest messages, oldest first."""

    statement = select(Conversation).where(
        Conversation.tenant_id == tenant_id, Conversation.updated_at >= since
    )
    if customer_ids is not None:
        statement = statement.where(Conversation.customer_id.in_(customer_ids))
    conversations = {c.id: c for c in session.scalars(statement)}
    pks = list(conversations)
    messages = (
        list(
            session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_pk.in_(pks))
                .order_by(ConversationMessage.id.desc())
                .limit(MAX_MESSAGES)
            )
        )
        if pks
        else []
    )
    messages.reverse()
    return conversations, messages


def _customer_names(session: Session, conversations: Iterable[Conversation]) -> dict[int, str]:
    ids = {c.customer_id for c in conversations}
    if not ids:
        return {}
    return {
        row.id: row.name for row in session.scalars(select(Customer).where(Customer.id.in_(ids)))
    }


def preview_monitor(
    session: Session,
    tenant_id: str,
    *,
    phrases: Sequence[str],
    sender_type: str,
    since: datetime,
    days: int,
    customer_ids: set[int] | None,
    limit: int = 5,
) -> MonitorPreview:
    """What a phrase monitor would flag over past conversations, without saving it."""

    conversations, messages = _window(session, tenant_id, since, customer_ids)
    hits = _phrase_hits(messages, phrases, sender_type)
    newest = sorted(hits.items(), key=lambda item: item[1].at, reverse=True)[:limit]
    names = _customer_names(session, (conversations[pk] for pk, _ in newest))
    return MonitorPreview(
        window_days=days,
        conversations=len(conversations),
        matched=len(hits),
        examples=[
            MonitorPreviewMatch(
                conversation_pk=pk,
                conversation_id=conversations[pk].conversation_id,
                customer_name=names.get(
                    conversations[pk].customer_id, f"Customer #{conversations[pk].customer_id}"
                ),
                detail=hit.detail,
                excerpt=hit.excerpt,
                at=hit.at,
            )
            for pk, hit in newest
        ],
    )


def quality(
    session: Session,
    tenant_id: str,
    runs: Sequence[AgentRunView],
    *,
    since: datetime,
    days: int,
    customer_ids: set[int] | None,
) -> QualityView:
    conversations, messages = _window(session, tenant_id, since, customer_ids)
    pks = list(conversations)
    pk_by_key = {(c.customer_id, c.conversation_id): c.id for c in conversations.values()}

    custom = list(
        session.scalars(
            select(QualityMonitor)
            .where(QualityMonitor.tenant_id == tenant_id)
            .order_by(QualityMonitor.id)
        )
    )
    ratings = (
        {
            row.conversation_pk: row
            for row in session.scalars(
                select(ConversationFeedback).where(ConversationFeedback.conversation_pk.in_(pks))
            )
        }
        if pks
        else {}
    )
    results: dict[str, tuple[str, str, dict[int, _Hit]]] = {
        "frustration": (
            "Frustrated customer",
            "high",
            _phrase_hits(messages, FRUSTRATION_PHRASES, "customer"),
        ),
        "repeat_clarification": (
            "Asked the customer twice",
            "medium",
            _run_hits(runs, pk_by_key, _repeat_clarification),
        ),
        "failed_action": (
            "Failed or degraded run",
            "high",
            _run_hits(runs, pk_by_key, _failed),
        ),
        "low_csat": (
            "Low rating",
            "high",
            {
                pk: _Hit(
                    f"Rated {row.rating} of 5",
                    row.comment or None,
                    row.updated_at,
                )
                for pk, row in ratings.items()
                if row.rating <= 2
            },
        ),
    }
    for monitor in custom:
        key = f"custom:{monitor.id}"
        hits = (
            _phrase_hits(messages, monitor.phrases, monitor.sender_type) if monitor.enabled else {}
        )
        results[key] = (monitor.name, monitor.severity, hits)

    reviews = {
        (row.monitor_key, row.conversation_pk): row
        for row in session.scalars(
            select(QualityFlagReview).where(QualityFlagReview.tenant_id == tenant_id)
        )
    }
    names = _customer_names(session, conversations.values())
    flags: list[FlagView] = []
    monitor_views: list[MonitorView] = []
    custom_by_key = {f"custom:{m.id}": m for m in custom}
    built_in_by_key = {item.key: item for item in BUILT_IN}
    for key, (name, severity, hits) in results.items():
        open_count = 0
        for pk, hit in hits.items():
            conversation = conversations[pk]
            review = reviews.get((key, pk))
            status = review.status if review else "open"
            open_count += status == "open"
            flags.append(
                FlagView(
                    monitor_key=key,
                    monitor_name=name,
                    severity=severity,
                    conversation_pk=pk,
                    conversation_id=conversation.conversation_id,
                    customer_id=conversation.customer_id,
                    customer_name=names.get(
                        conversation.customer_id, f"Customer #{conversation.customer_id}"
                    ),
                    detail=hit.detail,
                    excerpt=hit.excerpt,
                    at=hit.at,
                    status=status,
                    note=review.note if review else "",
                    run_id=hit.run_id,
                    intent=hit.intent,
                )
            )
        built_in = built_in_by_key.get(key)
        definition = custom_by_key.get(key)
        monitor_views.append(
            MonitorView(
                key=key,
                name=name,
                description=built_in.description
                if built_in
                else (definition.description if definition else ""),
                severity=severity,
                built_in=built_in is not None,
                enabled=True if built_in else bool(definition and definition.enabled),
                sender_type=definition.sender_type if definition else None,
                phrases=list(definition.phrases) if definition else [],
                open_flags=open_count,
                total_flags=len(hits),
            )
        )
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    flags.sort(
        key=lambda flag: (
            flag.status != "open",
            severity_rank.get(flag.severity, 3),
            -flag.at.timestamp(),
        )
    )
    return QualityView(
        window_days=days, conversations=len(conversations), monitors=monitor_views, flags=flags
    )


def review_flag(
    session: Session,
    tenant_id: str,
    *,
    monitor_key: str,
    conversation_pk: int,
    status: str,
    note: str,
    actor_id: str,
) -> QualityFlagReview | None:
    existing = session.scalar(
        select(QualityFlagReview).where(
            QualityFlagReview.tenant_id == tenant_id,
            QualityFlagReview.monitor_key == monitor_key,
            QualityFlagReview.conversation_pk == conversation_pk,
        )
    )
    if status == "open":
        if existing is not None:
            session.delete(existing)
            session.commit()
        return None
    row = existing or QualityFlagReview(
        tenant_id=tenant_id, monitor_key=monitor_key, conversation_pk=conversation_pk
    )
    row.status = status
    row.note = note
    row.reviewed_by = actor_id
    row.reviewed_at = utc_now_naive()
    if existing is None:
        session.add(row)
    session.commit()
    return row
