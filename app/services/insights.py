"""Improve loop: how the assistant performs, where it falls short, and what broke.

Everything here is computed from recorded runs, conversations, escalations and ratings. It is
read-only and bounded by the projection window; nothing calls a model.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.models.entities import (
    Conversation,
    ConversationMessage,
    ConversationOwner,
    ConversationOwnershipEvent,
    Escalation,
    MessageSender,
)
from app.models.workspace import ConversationFeedback
from app.rag.answer_generator import content_terms
from app.services.studio.procedures import PROCEDURES, is_clarification, is_failed
from app.ui.insights_schemas import (
    DailyPoint,
    ErrorCategory,
    ErrorLogView,
    ErrorRow,
    FlowRow,
    GapExample,
    GapView,
    PerformanceSummary,
    PerformanceView,
    TopicRow,
    UnansweredQuestion,
)
from app.ui.schemas import AgentRunView

REQUEST_TYPES = {procedure.intent: procedure.request_type for procedure in PROCEDURES}

KNOWLEDGE_INTENTS = {
    "refund_policy",
    "refund_eligibility",
    "cancellation_policy",
    "cancellation_explanation",
    "shipping_policy",
    "support_faq",
}
UNGROUNDED = {"insufficient_evidence", "ungrounded"}
FAILED_TOOL_STATUSES = {"failed", "error", "timeout", "unknown_outcome"}
MAX_EXAMPLES = 3


def _rate(part: int, total: int) -> float | None:
    return round(part / total, 4) if total else None


def _average(values: Sequence[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _conversations(
    session: Session, tenant_id: str, since: datetime, customer_ids: set[int] | None
) -> list[Conversation]:
    statement = select(Conversation).where(
        Conversation.tenant_id == tenant_id, Conversation.created_at >= since
    )
    if customer_ids is not None:
        statement = statement.where(Conversation.customer_id.in_(customer_ids))
    return list(session.scalars(statement))


def handed_off_conversations(session: Session, pks: Sequence[int]) -> set[int]:
    """Conversations a person was asked to join, by escalation or by an operator taking over."""

    if not pks:
        return set()
    escalated = set(
        session.scalars(
            select(Escalation.conversation_pk).where(Escalation.conversation_pk.in_(pks))
        )
    )
    taken = set(
        session.scalars(
            select(ConversationOwnershipEvent.conversation_pk).where(
                ConversationOwnershipEvent.conversation_pk.in_(pks),
                ConversationOwnershipEvent.to_owner.in_(
                    [ConversationOwner.QUEUED, ConversationOwner.HUMAN]
                ),
            )
        )
    )
    return {pk for pk in escalated | taken if pk is not None}


def performance(
    session: Session,
    tenant_id: str,
    runs: Sequence[AgentRunView],
    *,
    days: int,
    customer_ids: set[int] | None,
    now: datetime | None = None,
) -> PerformanceView:
    now = now or utc_now_naive()
    since = now - timedelta(days=days)
    conversations = _conversations(session, tenant_id, since, customer_ids)
    pks = [c.id for c in conversations]
    handed = handed_off_conversations(session, pks)
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
    runs_by_conversation: dict[tuple[int, str], list[AgentRunView]] = defaultdict(list)
    for run in runs:
        runs_by_conversation[(run.customer_id, run.conversation_id)].append(run)
    with_ai = {
        c.id for c in conversations if runs_by_conversation.get((c.customer_id, c.conversation_id))
    }
    ai_resolved = {pk for pk in with_ai if pk not in handed}

    daily: dict[date, dict[str, list[int] | int]] = {}
    for offset in range(days, -1, -1):
        daily[(now - timedelta(days=offset)).date()] = {
            "conversations": 0,
            "ai_resolved": 0,
            "handed_off": 0,
            "ratings": [],
        }
    for conversation in conversations:
        bucket = daily.get(conversation.created_at.date())
        if bucket is None:
            continue
        bucket["conversations"] += 1  # type: ignore[operator]
        bucket["ai_resolved"] += int(conversation.id in ai_resolved)  # type: ignore[operator]
        bucket["handed_off"] += int(conversation.id in handed)  # type: ignore[operator]
        if conversation.id in ratings:
            bucket["ratings"].append(ratings[conversation.id].rating)  # type: ignore[union-attr]

    pk_by_key = {(c.customer_id, c.conversation_id): c.id for c in conversations}
    topic_runs: dict[str, list[AgentRunView]] = defaultdict(list)
    for run in runs:
        topic_runs[run.intent].append(run)
    topics: list[TopicRow] = []
    for intent, items in topic_runs.items():
        topic_pks = {
            pk_by_key[key]
            for key in {(run.customer_id, run.conversation_id) for run in items}
            if key in pk_by_key
        }
        handoffs = len(topic_pks & handed)
        topics.append(
            TopicRow(
                intent=intent,
                runs=len(items),
                conversations=len({(run.customer_id, run.conversation_id) for run in items}),
                handoffs=handoffs,
                clarifications=sum(1 for run in items if is_clarification(run)),
                failures=sum(1 for run in items if is_failed(run)),
                handoff_rate=_rate(handoffs, len(topic_pks)),
                csat_average=_average([ratings[pk].rating for pk in topic_pks if pk in ratings]),
            )
        )
    topics.sort(key=lambda row: (-row.runs, row.intent))

    flows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"conversations": 0, "ai_resolved": 0, "routed": 0, "handled_by_team": 0}
    )
    for conversation in conversations:
        own = runs_by_conversation.get((conversation.customer_id, conversation.conversation_id))
        if own:
            first = min(own, key=lambda run: run.started_at)
            kind = REQUEST_TYPES.get(first.intent, "other")
        else:
            kind = "not_seen_by_ai"
        row = flows[kind]
        row["conversations"] += 1
        if conversation.id in ai_resolved:
            row["ai_resolved"] += 1
        elif conversation.id in handed and own:
            row["routed"] += 1
        elif conversation.id in handed:
            row["handled_by_team"] += 1

    all_ratings = list(ratings.values())
    return PerformanceView(
        summary=PerformanceSummary(
            window_days=days,
            conversations=len(conversations),
            ai_resolved=len(ai_resolved),
            handed_off=len(handed),
            ai_resolution_rate=_rate(len(ai_resolved), len(conversations)),
            handoff_rate=_rate(len(handed), len(conversations)),
            csat_average=_average([row.rating for row in all_ratings]),
            csat_responses=len(all_ratings),
            csat_ai_average=_average([r.rating for r in all_ratings if r.resolved_by == "ai"]),
            csat_human_average=_average(
                [r.rating for r in all_ratings if r.resolved_by == "human"]
            ),
            ai_runs=len(runs),
            failed_runs=sum(1 for run in runs if is_failed(run)),
        ),
        daily=[
            DailyPoint(
                day=day,
                conversations=int(values["conversations"]),  # type: ignore[arg-type]
                ai_resolved=int(values["ai_resolved"]),  # type: ignore[arg-type]
                handed_off=int(values["handed_off"]),  # type: ignore[arg-type]
                csat_average=_average(values["ratings"]),  # type: ignore[arg-type]
            )
            for day, values in daily.items()
        ],
        topics=topics,
        flows=[
            FlowRow(request_type=kind, **values)
            for kind, values in sorted(flows.items(), key=lambda item: -item[1]["conversations"])
        ],
    )


def customer_questions(session: Session, run_ids: Iterable[str]) -> dict[str, ConversationMessage]:
    """The customer message that started each run, already DLP-redacted at write time."""

    ids = list(set(run_ids))
    if not ids:
        return {}
    rows = session.scalars(
        select(ConversationMessage).where(
            ConversationMessage.agent_run_id.in_(ids),
            ConversationMessage.sender_type == MessageSender.CUSTOMER,
        )
    )
    return {row.agent_run_id: row for row in rows if row.agent_run_id}


def _examples(
    runs: Sequence[AgentRunView], questions: dict[str, ConversationMessage]
) -> list[GapExample]:
    examples: list[GapExample] = []
    for run in sorted(runs, key=lambda item: item.started_at, reverse=True):
        message = questions.get(run.run_id)
        if message is None:
            continue
        examples.append(
            GapExample(
                question=message.content,
                conversation_pk=message.conversation_pk,
                run_id=run.run_id,
                at=run.started_at,
            )
        )
        if len(examples) == MAX_EXAMPLES:
            break
    return examples


def _ungrounded(run: AgentRunView) -> bool:
    knowledge_run = run.request_type == "knowledge_only" or run.intent in KNOWLEDGE_INTENTS
    if not knowledge_run:
        return False
    return (
        not run.rag_documents
        or run.answer_grounding.status in UNGROUNDED
        or (run.failure_category or "").startswith(("retriev", "knowledge", "rag"))
    )


_ARTICLES = {
    procedure.intent: procedure.knowledge[0] for procedure in PROCEDURES if procedure.knowledge
}


def unanswered_questions(
    session: Session, runs: Sequence[AgentRunView]
) -> list[UnansweredQuestion]:
    """Questions the assistant could not ground in an article, newest first, repeats merged.

    The wording is the customer's own message, already redacted when it was stored, so the
    list reads the way customers ask and can become an article's headings as it is.
    """

    misses = [run for run in runs if run.operation_type == "agent_request" and _ungrounded(run)]
    questions = customer_questions(session, (run.run_id for run in misses))
    merged: dict[str, UnansweredQuestion] = {}
    for run in sorted(misses, key=lambda item: item.started_at, reverse=True):
        message = questions.get(run.run_id)
        if message is None or not message.content.strip():
            continue
        key = " ".join(message.content.casefold().split()).rstrip("?.! ")
        if key in merged:
            merged[key].asked += 1
            continue
        merged[key] = UnansweredQuestion(
            question=message.content.strip(),
            intent=run.intent,
            asked=1,
            last_asked_at=run.started_at,
            conversation_pk=message.conversation_pk,
            run_id=run.run_id,
            article_id=_ARTICLES.get(run.intent),
        )
    return sorted(merged.values(), key=lambda item: (-item.asked, -item.last_asked_at.timestamp()))


def _disabled_tool(run: AgentRunView) -> str | None:
    for event in run.policy:
        if "action_disabled_by_operator" in event.reason_codes:
            return event.tool_name
    return None


def gaps(
    session: Session, tenant_id: str, runs: Sequence[AgentRunView], *, since: datetime
) -> list[GapView]:
    questions = customer_questions(session, (run.run_id for run in runs))
    found: list[GapView] = []

    content: dict[str, list[AgentRunView]] = defaultdict(list)
    clarify: dict[str, list[AgentRunView]] = defaultdict(list)
    outside: list[AgentRunView] = []
    disabled: dict[str, list[AgentRunView]] = defaultdict(list)
    for run in runs:
        if _ungrounded(run):
            content[run.intent].append(run)
        if is_clarification(run) and run.intent not in {"unknown"}:
            clarify[run.intent].append(run)
        if run.intent in {"unknown", "capability_question"}:
            outside.append(run)
        tool = _disabled_tool(run)
        if tool:
            disabled[tool].append(run)

    for intent, items in content.items():
        found.append(
            GapView(
                kind="content",
                key=f"content:{intent}",
                title=f"No grounded answer for {intent.replace('_', ' ')} questions",
                description="Retrieval found no passage good enough to cite. "
                "Add or expand an article.",
                intent=intent,
                count=len(items),
                fix="knowledge",
                examples=_examples(items, questions),
            )
        )
    for intent, items in clarify.items():
        found.append(
            GapView(
                kind="clarification",
                key=f"clarification:{intent}",
                title=f"Had to ask again on {intent.replace('_', ' ')} requests",
                description="The request was understood but key details were missing or unclear. "
                "Guidance can tell the assistant how to read these requests.",
                intent=intent,
                count=len(items),
                fix="guidance",
                examples=_examples(items, questions),
            )
        )
    if outside:
        found.append(
            GapView(
                kind="action",
                key="action:outside",
                title="Requests outside what the assistant can do",
                description="Customers asked for something no procedure covers. Consider a new "
                "action, an article, or guidance to hand these off quickly.",
                intent="unknown",
                count=len(outside),
                fix="procedures",
                examples=_examples(outside, questions),
            )
        )
    for tool, items in disabled.items():
        found.append(
            GapView(
                kind="action",
                key=f"action:disabled:{tool}",
                title=f"Customers needed {tool.replace('_', ' ')}, which is switched off",
                description="Your team switched this action off, so these requests could not be "
                "completed by the assistant.",
                intent=None,
                count=len(items),
                fix="actions",
                examples=_examples(items, questions),
            )
        )

    escalations = list(
        session.scalars(
            select(Escalation).where(
                Escalation.tenant_id == tenant_id, Escalation.created_at >= since
            )
        )
    )
    reasons = _group_handoff_reasons(escalations)
    handoff_pks = [e.conversation_pk for e in escalations if e.conversation_pk is not None]
    last_customer: dict[int, ConversationMessage] = {}
    if handoff_pks:
        for message in session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_pk.in_(handoff_pks),
                ConversationMessage.sender_type == MessageSender.CUSTOMER,
            )
            .order_by(ConversationMessage.sequence)
        ):
            escalation_at = next(
                (e.created_at for e in escalations if e.conversation_pk == message.conversation_pk),
                None,
            )
            if escalation_at is None or message.created_at <= escalation_at + timedelta(seconds=5):
                last_customer[message.conversation_pk] = message
    for reason, escalated in reasons.items():
        examples = [
            GapExample(
                question=last_customer[item.conversation_pk].content,
                conversation_pk=item.conversation_pk,
                run_id=last_customer[item.conversation_pk].agent_run_id,
                at=item.created_at,
            )
            for item in sorted(escalated, key=lambda e: e.created_at, reverse=True)
            if item.conversation_pk is not None and item.conversation_pk in last_customer
        ][:MAX_EXAMPLES]
        found.append(
            GapView(
                kind="handoff",
                key=f"handoff:{reason}",
                title=f"Handed to a person: {reason}",
                description="Conversations the assistant passed to your team for this reason. "
                "Frequent reasons are candidates for a procedure or an article.",
                intent="human_escalation",
                count=len(escalated),
                fix="procedures",
                examples=examples,
            )
        )
    found.sort(key=lambda gap: (-gap.count, gap.kind, gap.key))
    return found


# Words every hand-off reason shares; they say a person was asked for, not why.
_HANDOFF_FILLER = {
    "agent",
    "asked",
    "assistance",
    "customer",
    "help",
    "human",
    "immediate",
    "person",
    "real",
    "report",
    "request",
    "someone",
    "speak",
    "support",
    "talk",
    "user",
    "want",
}
_HANDOFF_SIMILARITY = 0.25
_ASKED_FOR_PERSON = "asked for a person"


def _group_handoff_reasons(escalations: Sequence[Escalation]) -> dict[str, list[Escalation]]:
    """Group escalations whose free-text reasons talk about the same thing.

    Reasons are written by the model, so "unexpected charge" and "disputed $40 charge"
    arrive as different strings; grouping by exact text would split one theme into many.
    """

    clusters: list[tuple[list[set[str]], list[Escalation], list[str]]] = []
    for escalation in sorted(escalations, key=lambda item: item.created_at):
        text = escalation.reason.strip().lower()[:80]
        terms = {term for term in content_terms(text) if term not in _HANDOFF_FILLER}
        home = None
        if terms:
            home = next(
                (
                    cluster
                    for cluster in clusters
                    if any(
                        member and len(terms & member) / len(terms | member) >= _HANDOFF_SIMILARITY
                        for member in cluster[0]
                    )
                ),
                None,
            )
        else:
            home = next((cluster for cluster in clusters if not any(cluster[0])), None)
        if home is None:
            home = ([], [], [])
            clusters.append(home)
        home[0].append(terms)
        home[1].append(escalation)
        home[2].append(text if terms else _ASKED_FOR_PERSON)
    grouped: dict[str, list[Escalation]] = {}
    for _, members, texts in clusters:
        label = max(dict.fromkeys(texts), key=texts.count) or "no reason given"
        grouped.setdefault(label, []).extend(members)
    return grouped


def _error_category(run: AgentRunView) -> str | None:
    failed_tools = [tool.name for tool in run.tools if tool.status in FAILED_TOOL_STATUSES]
    if run.failure_category:
        return run.failure_category
    if run.status == "error":
        return "runtime_error"
    if failed_tools:
        return "tool_failure"
    if run.degraded_components:
        return "degraded_" + run.degraded_components[0]
    return None


def error_log(
    runs: Sequence[AgentRunView], *, days: int, now: datetime | None = None
) -> ErrorLogView:
    now = now or utc_now_naive()
    rows: list[ErrorRow] = []
    for run in runs:
        category = _error_category(run)
        if category is None:
            continue
        rows.append(
            ErrorRow(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                customer_id=run.customer_id,
                at=run.started_at,
                intent=run.intent,
                status=run.status,
                category=category,
                failed_tools=[t.name for t in run.tools if t.status in FAILED_TOOL_STATUSES],
                degraded_components=run.degraded_components,
                recovery_action=run.recovery_action,
            )
        )
    rows.sort(key=lambda row: row.at, reverse=True)
    counts = Counter(row.category for row in rows)
    last_seen: dict[str, datetime] = {}
    for row in rows:
        last_seen.setdefault(row.category, row.at)
    per_day: dict[date, Counter[str]] = {
        (now - timedelta(days=offset)).date(): Counter() for offset in range(days, -1, -1)
    }
    for row in rows:
        if row.at.date() in per_day:
            per_day[row.at.date()][row.category] += 1
    return ErrorLogView(
        window_days=days,
        total_runs=len(runs),
        failed_runs=len(rows),
        categories=[
            ErrorCategory(category=category, count=count, last_seen=last_seen[category])
            for category, count in counts.most_common()
        ],
        daily=[
            {"day": day.isoformat(), "total": sum(counter.values()), **dict(counter)}
            for day, counter in per_day.items()
        ],
        errors=rows[:500],
    )
