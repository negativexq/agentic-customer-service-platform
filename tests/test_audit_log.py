from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Conversation, ConversationOwner, ConversationOwnershipEvent, Escalation
from app.models.entities import ConversationMessage
from app.services.audit import audit_log
from app.ui.schemas import AgentRunView

START = datetime(2026, 11, 23, 9, 0, 0)


def _run(run_id: str, status: str, minutes: int, **extra: object) -> AgentRunView:
    return AgentRunView.model_validate(
        {
            "run_id": run_id,
            "request_id": run_id,
            "conversation_id": "conv-1",
            "customer_id": 1,
            "actor_id": "customer-1",
            "actor_type": "customer",
            "intent": "refund_request",
            "request_type": "write_action",
            "status": status,
            "started_at": START + timedelta(minutes=minutes),
            "duration_ms": 10,
            "memory": {"item_count": 0, "keys": [], "types": []},
            **extra,
        }
    )


def _conversation(session: Session) -> Conversation:
    conversation = Conversation(
        tenant_id="default",
        customer_id=1,
        conversation_id="conv-1",
        owner=ConversationOwner.CLOSED,
        ownership_version=3,
        created_at=START,
        updated_at=START + timedelta(minutes=30),
    )
    session.add(conversation)
    session.flush()
    moves = [
        (5, "ai", "queued", "customer_escalation", "operator-1"),
        (12, "queued", "human", "operator_claimed", "operator-1"),
        (30, "human", "closed", "operator_resolved", "operator-1"),
    ]
    for minutes, source, target, reason, actor in moves:
        session.add(
            ConversationOwnershipEvent(
                tenant_id="default",
                conversation_pk=conversation.id,
                actor_id=actor,
                from_owner=source,
                to_owner=target,
                reason_code=reason,
                created_at=START + timedelta(minutes=minutes),
            )
        )
    session.add(
        Escalation(
            tenant_id="default",
            customer_id=1,
            conversation_pk=conversation.id,
            reason="Customer asked for a person",
            priority="high",
            summary="",
            status="open",
            created_at=START + timedelta(minutes=5),
        )
    )
    session.add(
        ConversationMessage(
            tenant_id="default",
            conversation_pk=conversation.id,
            customer_id=1,
            sequence=4,
            sender_type="operator",
            sender_actor_id="operator-1",
            sender_display_name="Sam Rivera",
            content="Hi, I'm on it.",
            redaction_state="none",
            created_at=START + timedelta(minutes=14),
            expires_at=START + timedelta(days=30),
        )
    )
    session.commit()
    return conversation


def test_conversation_trail_reads_in_order_with_names_and_reasons(db_session: Session) -> None:
    conversation = _conversation(db_session)
    runs = [
        _run("run-a", "declined", 2, decision_reason="Duplicate business action was prevented."),
        _run(
            "run-b",
            "blocked",
            3,
            decision_reason="instruction_override_attempt",
            security_signal="instruction_override_attempt",
        ),
    ]

    view = audit_log(
        db_session,
        "default",
        runs,
        since=START - timedelta(days=1),
        conversation_pk=conversation.id,
    )
    trail = list(reversed(view.events))

    assert [event.action for event in trail] == [
        "conversation.started",
        "assistant.declined",
        "assistant.blocked",
        "handoff.queued",
        "operator.claimed",
        "operator.replied",
        "conversation.closed",
    ]
    assert trail[0].actor.type == "customer"
    assert trail[1].detail == "Already requested, so it was not done twice"
    assert trail[2].kind == "safety"
    assert trail[3].actor.type == "ai"
    assert trail[3].detail == "High priority · Customer asked for a person"
    assert trail[4].actor.name == "Sam Rivera"
    assert trail[6].summary == "Closed the conversation"
    assert trail[6].actor.name == "Sam Rivera"
    assert view.counts["handoff"] == 2


def test_filters_by_kind_and_hides_other_customers(db_session: Session) -> None:
    _conversation(db_session)
    since = START - timedelta(days=1)

    only_handoffs = audit_log(db_session, "default", [], since=since, kinds=["handoff"])
    assert {event.kind for event in only_handoffs.events} == {"handoff"}

    out_of_scope = audit_log(db_session, "default", [], since=since, customer_ids={2})
    assert out_of_scope.events == []


def test_search_runs_before_the_limit(db_session: Session) -> None:
    _conversation(db_session)
    noise = [_run(f"run-{index}", "completed", 1) for index in range(20)]

    view = audit_log(
        db_session,
        "default",
        noise,
        since=START - timedelta(days=1),
        search="closed the conversation",
        limit=5,
    )

    assert [event.action for event in view.events] == ["conversation.closed"]
    assert view.events[0].actor.name == "Sam Rivera"
