from collections.abc import Callable, Generator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import HANDOFF_ACKNOWLEDGEMENT, AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, StructuredDecision
from app.core.database import Base
from app.models import (
    Conversation,
    ConversationOwner,
    ConversationOwnershipEvent,
    Customer,
    Escalation,
    EscalationPriority,
    EscalationStatus,
    RefundRequest,
    SupportTicket,
    Tenant,
    TicketStatus,
)
from app.resilience.errors import UnknownWriteOutcomeError
from app.services.conversations import (
    ConversationOwnershipChangedError,
    claim_conversation,
    queue_for_human,
    release_conversation,
    resolve_conversation,
)
from scripts.qa_fixtures import QA_TENANT_ID
from scripts.seed import seed_into

ESCALATION_ARGUMENTS: dict[str, object] = {
    "customer_id": 1,
    "ticket_id": 1,
    "reason": "customer requested",
    "priority": "high",
    "summary": "Customer asked for a person",
}


def _escalation_decision() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.HUMAN_ESCALATION,
        request_type=AgentRequestType.ESCALATION,
        tool_name="escalate_to_human",
        arguments=ESCALATION_ARGUMENTS,
        reason="handoff test",
    )


def _ticket_decision() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.TICKET_CREATE,
        request_type=AgentRequestType.WRITE_ACTION,
        tool_name="create_support_ticket",
        arguments={
            "customer_id": 1,
            "order_id": 3,
            "category": "delivery",
            "description": "My parcel is late.",
        },
        reason="handoff test",
    )


def _orders_decision() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.ORDER_LIST,
        request_type=AgentRequestType.READ_ACTION,
        tool_name="get_customer_orders",
        arguments={"customer_id": 1},
        reason="handoff test",
    )


def _refund_decision() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.REFUND_REQUEST,
        request_type=AgentRequestType.WRITE_ACTION,
        tool_name="request_refund",
        arguments={"customer_id": 1, "order_id": 2, "reason": "Item arrived damaged."},
        reason="handoff test",
    )


class _DuringDecisionProvider(FakeDecisionProvider):
    """Change server state while the LLM is working, as a concurrent operator would."""

    def __init__(self, decisions: Sequence[StructuredDecision], during: Callable[[], None]) -> None:
        super().__init__(decisions)
        self._during = during

    def decide(self, *, messages, customer_id, memory_context=None):  # type: ignore[no-untyped-def]
        self._during()
        return super().decide(
            messages=messages, customer_id=customer_id, memory_context=memory_context
        )


def _conversation(session: Session, conversation_id: str, customer_id: int = 1) -> Conversation:
    session.expire_all()
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.conversation_id == conversation_id,
            Conversation.customer_id == customer_id,
        )
    )
    assert conversation is not None
    return conversation


def _escalate(session: Session, conversation_id: str) -> Conversation:
    AgentRuntime(provider=FakeDecisionProvider([_escalation_decision()])).run(
        conversation_id=conversation_id,
        customer_id=1,
        message="I need a human.",
        session=session,
    )
    return _conversation(session, conversation_id)


def _claim(session: Session, conversation: Conversation, actor_id: str = "operator-a") -> None:
    claim_conversation(
        session,
        tenant_id=conversation.tenant_id,
        conversation_pk=conversation.id,
        actor_id=actor_id,
        expected_version=conversation.ownership_version,
    )
    session.commit()


def _force_ownership_change(session: Session, conversation_id: str) -> None:
    session.execute(
        update(Conversation)
        .where(Conversation.conversation_id == conversation_id)
        .values(
            owner=ConversationOwner.HUMAN,
            ownership_version=Conversation.ownership_version + 1,
        )
    )
    session.commit()


def test_escalation_silences_ai_in_same_transaction_and_keeps_its_own_reply(
    db_session: Session,
) -> None:
    runtime = AgentRuntime(provider=FakeDecisionProvider([_escalation_decision()]))

    response = runtime.run(
        conversation_id="handoff-escalate",
        customer_id=1,
        message="I need a human.",
        session=db_session,
    )

    conversation = _conversation(db_session, "handoff-escalate")
    escalation = db_session.scalar(select(Escalation))
    assert response.conversation_owner == "queued"
    assert response.message != HANDOFF_ACKNOWLEDGEMENT
    assert response.tool_call is not None and response.tool_call.status == "executed"
    assert conversation.owner == ConversationOwner.QUEUED
    assert conversation.ownership_version == 1
    assert escalation is not None and escalation.conversation_pk == conversation.id
    event = db_session.scalar(select(ConversationOwnershipEvent))
    assert event is not None
    assert (event.from_owner, event.to_owner, event.reason_code) == (
        "ai",
        "queued",
        "customer_escalation",
    )


@pytest.fixture
def transactional_session() -> Generator[Session, None, None]:
    """SQLite with real transactions; pysqlite otherwise commits an outermost savepoint."""

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def disable_implicit_transactions(connection: Any, _: object) -> None:
        connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def begin_transaction(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Customer(id=1, name="Test Customer", email="test@example.com"))
        session.flush()
        session.add(
            SupportTicket(
                id=1,
                customer_id=1,
                category="delivery",
                status=TicketStatus.OPEN,
                description="Where is my order?",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def test_failed_escalation_commit_leaves_ai_owner_and_no_escalation(
    transactional_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_commit(session: Session, operation: str) -> None:
        raise UnknownWriteOutcomeError(operation)

    monkeypatch.setattr("app.agent.nodes.human.commit_business_write", fail_commit)
    session = transactional_session
    runtime = AgentRuntime(provider=FakeDecisionProvider([_escalation_decision()]))

    response = runtime.run(
        conversation_id="handoff-commit-failure",
        customer_id=1,
        message="I need a human.",
        session=session,
    )

    conversation = _conversation(session, "handoff-commit-failure")
    assert response.write_outcome_unknown is True
    assert conversation.owner == ConversationOwner.AI
    assert conversation.ownership_version == 0
    assert session.scalar(select(func.count()).select_from(Escalation)) == 0
    assert session.scalar(select(func.count()).select_from(ConversationOwnershipEvent)) == 0


@pytest.mark.parametrize("claimed", [False, True])
def test_llm_is_not_called_while_a_human_path_owns_the_conversation(
    db_session: Session, claimed: bool
) -> None:
    conversation_id = f"handoff-gate-{claimed}"
    conversation = _escalate(db_session, conversation_id)
    if claimed:
        _claim(db_session, conversation)
    provider = FakeDecisionProvider([_ticket_decision()])
    tickets_before = db_session.scalar(select(func.count()).select_from(SupportTicket))

    response = AgentRuntime(provider=provider).run(
        conversation_id=conversation_id,
        customer_id=1,
        message="yes",
        session=db_session,
    )

    assert provider.calls == []
    assert response.message == HANDOFF_ACKNOWLEDGEMENT
    assert response.conversation_owner == ("human" if claimed else "queued")
    assert response.pending_action is None
    assert db_session.scalar(select(func.count()).select_from(SupportTicket)) == tickets_before


def test_failed_handoff_projection_does_not_fail_the_customer_turn(db_session: Session) -> None:
    class BrokenRepository:
        def upsert(self, projection: object) -> None:
            raise ConnectionError("projection store unavailable")

    _escalate(db_session, "handoff-projection-failure")
    runtime = AgentRuntime(
        provider=FakeDecisionProvider([]),
        projection_repository=BrokenRepository(),  # type: ignore[arg-type]
    )

    response = runtime.run(
        conversation_id="handoff-projection-failure",
        customer_id=1,
        message="hello?",
        session=db_session,
    )

    assert response.message == HANDOFF_ACKNOWLEDGEMENT


def test_ownership_change_during_run_blocks_business_write_and_reply(db_session: Session) -> None:
    conversation_id = "handoff-stale-write"
    tickets_before = db_session.scalar(select(func.count()).select_from(SupportTicket))
    runtime = AgentRuntime(
        provider=_DuringDecisionProvider(
            [_ticket_decision()],
            lambda: _force_ownership_change(db_session, conversation_id),
        )
    )

    response = runtime.run(
        conversation_id=conversation_id,
        customer_id=1,
        message="Open a ticket for my late parcel.",
        session=db_session,
    )

    assert db_session.scalar(select(func.count()).select_from(SupportTicket)) == tickets_before
    assert response.message == HANDOFF_ACKNOWLEDGEMENT
    assert response.conversation_owner == "human"
    assert response.tool_call is None
    assert response.decision_reason == "conversation_ownership_changed"


def test_ownership_change_during_run_withholds_read_only_reply(db_session: Session) -> None:
    conversation_id = "handoff-stale-read"
    runtime = AgentRuntime(
        provider=_DuringDecisionProvider(
            [_orders_decision()],
            lambda: _force_ownership_change(db_session, conversation_id),
        )
    )

    response = runtime.run(
        conversation_id=conversation_id,
        customer_id=1,
        message="Show my orders.",
        session=db_session,
    )

    assert response.message == HANDOFF_ACKNOWLEDGEMENT
    assert response.tool_call is None
    assert "25.00" not in response.message


def test_failed_ownership_recheck_withholds_reply_without_failing_the_run(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(session: Session, context: object) -> None:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr("app.agent.runtime.current_ownership", unavailable)
    runtime = AgentRuntime(provider=FakeDecisionProvider([_ticket_decision()]))

    response = runtime.run(
        conversation_id="handoff-recheck-failure",
        customer_id=1,
        message="Open a ticket for my late parcel.",
        session=db_session,
    )

    assert response.message == HANDOFF_ACKNOWLEDGEMENT
    assert response.tool_call is None


def test_pending_action_from_before_handoff_cannot_be_confirmed_after_release(
    db_session: Session,
) -> None:
    conversation_id = "handoff-pending"
    first = AgentRuntime(provider=FakeDecisionProvider([_refund_decision()]))
    pending = first.run(
        conversation_id=conversation_id,
        customer_id=1,
        message="Refund order 2.",
        session=db_session,
    )
    assert pending.pending_action is not None

    conversation = _conversation(db_session, conversation_id)
    db_session.add(
        Escalation(
            customer_id=1,
            reason="operator review",
            priority=EscalationPriority.HIGH,
            summary="Operator review",
            status=EscalationStatus.QUEUED,
            conversation_pk=conversation.id,
        )
    )
    db_session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(owner=ConversationOwner.QUEUED, ownership_version=1)
    )
    db_session.commit()
    conversation = _conversation(db_session, conversation_id)
    _claim(db_session, conversation)
    conversation = _conversation(db_session, conversation_id)
    release_conversation(
        db_session,
        tenant_id=conversation.tenant_id,
        conversation_pk=conversation.id,
        actor_id="operator-a",
        expected_version=conversation.ownership_version,
    )
    db_session.commit()

    confirmed = first.run(
        conversation_id=conversation_id,
        customer_id=1,
        message="yes",
        session=db_session,
    )

    assert confirmed.tool_call is None or confirmed.tool_call.status != "executed"
    assert db_session.scalar(select(func.count()).select_from(RefundRequest)) == 0


def test_claim_records_assignment_and_rejects_a_stale_second_claim(db_session: Session) -> None:
    conversation = _escalate(db_session, "handoff-claim")
    stale_version = conversation.ownership_version

    _claim(db_session, conversation, "operator-a")

    claimed = _conversation(db_session, "handoff-claim")
    escalation = db_session.scalar(select(Escalation))
    assert claimed.owner == ConversationOwner.HUMAN
    assert claimed.assignee_actor_id == "operator-a"
    assert escalation is not None and escalation.status == EscalationStatus.ASSIGNED
    assert escalation.assignee_actor_id == "operator-a"
    assert escalation.assigned_at is not None
    with pytest.raises(ConversationOwnershipChangedError):
        claim_conversation(
            db_session,
            tenant_id=claimed.tenant_id,
            conversation_pk=claimed.id,
            actor_id="operator-b",
            expected_version=stale_version,
        )


def test_only_the_assignee_can_release_or_resolve(db_session: Session) -> None:
    conversation = _escalate(db_session, "handoff-assignee")
    _claim(db_session, conversation, "operator-a")
    claimed = _conversation(db_session, "handoff-assignee")

    for action in (release_conversation, resolve_conversation):
        with pytest.raises(ConversationOwnershipChangedError):
            action(
                db_session,
                tenant_id=claimed.tenant_id,
                conversation_pk=claimed.id,
                actor_id="operator-b",
                expected_version=claimed.ownership_version,
            )


def test_transitions_cannot_cross_tenants(db_session: Session) -> None:
    conversation = _escalate(db_session, "handoff-tenant")

    with pytest.raises(ConversationOwnershipChangedError):
        claim_conversation(
            db_session,
            tenant_id="other-tenant",
            conversation_pk=conversation.id,
            actor_id="operator-a",
            expected_version=conversation.ownership_version,
        )


def test_release_returns_conversation_to_ai(db_session: Session) -> None:
    conversation = _escalate(db_session, "handoff-release")
    _claim(db_session, conversation)
    claimed = _conversation(db_session, "handoff-release")

    release_conversation(
        db_session,
        tenant_id=claimed.tenant_id,
        conversation_pk=claimed.id,
        actor_id="operator-a",
        expected_version=claimed.ownership_version,
    )
    db_session.commit()

    released = _conversation(db_session, "handoff-release")
    escalation = db_session.scalar(select(Escalation))
    assert released.owner == ConversationOwner.AI
    assert released.assignee_actor_id is None
    assert released.ai_resumed_at is not None
    assert escalation is not None and escalation.status == EscalationStatus.RESOLVED
    assert escalation.resolved_at is not None

    provider = FakeDecisionProvider([_orders_decision()])
    response = AgentRuntime(provider=provider).run(
        conversation_id="handoff-release",
        customer_id=1,
        message="Show my orders.",
        session=db_session,
    )
    assert len(provider.calls) == 1
    assert response.conversation_owner == "ai"


def test_new_message_reopens_a_closed_conversation_to_ai(db_session: Session) -> None:
    conversation = _escalate(db_session, "handoff-reopen")
    _claim(db_session, conversation)
    claimed = _conversation(db_session, "handoff-reopen")
    resolve_conversation(
        db_session,
        tenant_id=claimed.tenant_id,
        conversation_pk=claimed.id,
        actor_id="operator-a",
        expected_version=claimed.ownership_version,
    )
    db_session.commit()
    assert _conversation(db_session, "handoff-reopen").owner == ConversationOwner.CLOSED

    provider = FakeDecisionProvider([_orders_decision()])
    response = AgentRuntime(provider=provider).run(
        conversation_id="handoff-reopen",
        customer_id=1,
        message="Show my orders.",
        session=db_session,
    )

    reopened = _conversation(db_session, "handoff-reopen")
    assert len(provider.calls) == 1
    assert response.conversation_owner == "ai"
    assert reopened.owner == ConversationOwner.AI
    assert reopened.ai_resumed_at is not None
    reasons = db_session.scalars(
        select(ConversationOwnershipEvent.reason_code)
        .where(ConversationOwnershipEvent.conversation_pk == reopened.id)
        .order_by(ConversationOwnershipEvent.id)
    ).all()
    assert reasons == [
        "customer_escalation",
        "operator_claimed",
        "operator_resolved",
        "customer_reopened",
    ]


def test_same_conversation_id_is_separate_per_customer(db_session: Session) -> None:
    _escalate(db_session, "handoff-shared-id")
    provider = FakeDecisionProvider(
        [
            StructuredDecision(
                intent=Intent.ORDER_LIST,
                request_type=AgentRequestType.READ_ACTION,
                tool_name="get_customer_orders",
                arguments={"customer_id": 2},
                reason="handoff test",
            )
        ]
    )

    response = AgentRuntime(provider=provider).run(
        conversation_id="handoff-shared-id",
        customer_id=2,
        message="Show my orders.",
        session=db_session,
    )

    assert len(provider.calls) == 1
    assert response.conversation_owner == "ai"
    assert _conversation(db_session, "handoff-shared-id", customer_id=2).owner == (
        ConversationOwner.AI
    )
    assert _conversation(db_session, "handoff-shared-id", customer_id=1).owner == (
        ConversationOwner.QUEUED
    )


def test_chat_route_reports_owner_and_never_echoes_customer_message(
    client: TestClient, db_session: Session
) -> None:
    _escalate(db_session, "handoff-route")

    response = client.post(
        "/agent/chat",
        json={
            "conversation_id": "handoff-route",
            "customer_id": 1,
            "message": "PRIVATE_SENTINEL are you there?",
        },
    )

    assert response.status_code == 200
    assert response.json()["conversation_owner"] == "queued"
    assert response.json()["message"] == HANDOFF_ACKNOWLEDGEMENT
    assert "PRIVATE_SENTINEL" not in response.text

    run = client.get(f"/ui/agent-runs/{response.json()['agent_run_id']}")
    assert run.status_code == 200
    assert run.json()["decision_reason"] == "conversation_handoff_active"
    assert "PRIVATE_SENTINEL" not in run.text


def test_ownership_events_never_store_message_text() -> None:
    assert {column.name for column in ConversationOwnershipEvent.__table__.columns} == {
        "id",
        "tenant_id",
        "conversation_pk",
        "actor_id",
        "from_owner",
        "to_owner",
        "reason_code",
        "created_at",
    }


def test_demo_seed_reruns_after_a_conversation_was_handed_off() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection: Any, _: object) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id=QA_TENANT_ID, name="Demo"))
        seed_into(session)
        customer_id = session.scalars(select(Customer.id).order_by(Customer.id)).first()
        assert customer_id is not None
        conversation = Conversation(
            tenant_id=QA_TENANT_ID,
            customer_id=customer_id,
            conversation_id="seed-rerun",
            owner=ConversationOwner.AI,
        )
        escalation = Escalation(
            customer_id=customer_id,
            tenant_id=QA_TENANT_ID,
            reason="seed rerun",
            priority=EscalationPriority.HIGH,
            summary="seed rerun",
            status=EscalationStatus.QUEUED,
        )
        session.add_all([conversation, escalation])
        session.flush()
        queue_for_human(session, conversation, escalation_id=escalation.id, actor_id="seed-test")
        session.flush()

        seed_into(session)

        assert session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert session.scalar(select(func.count()).select_from(ConversationOwnershipEvent)) == 0
