import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentErrorCategory, AgentRequestType, Intent, StructuredDecision
from app.auth.models import ActorType, Principal
from app.core.config import Settings
from app.core.context import ExecutionContext
from app.core.database import Base
from app.memory.schemas import MemoryCandidate, MemoryType
from app.memory.service import MemoryService
from app.models import Customer, Order
from app.models.entities import OrderStatus
from app.persistence.checkpoint import (
    CHECKPOINT_ALLOWED_MSGPACK_TYPES,
    CheckpointBackend,
    CheckpointDeserializationError,
    MemoryCheckpointProvider,
    build_checkpoint_provider,
    build_checkpoint_serializer,
    checkpoint_thread_id,
    checkpoint_thread_id_hash,
)
from app.policies.models import (
    PendingAction,
    PendingActionStatus,
    PolicyDecision,
    PolicyOutcome,
)


class UnregisteredCheckpointType(BaseModel):
    value: str


def execution_context(
    *,
    actor_id: str = "customer-one",
    customer_id: int = 1,
    conversation_id: str = "durable-thread",
) -> ExecutionContext:
    return ExecutionContext(
        request_id=f"request-{actor_id}-{customer_id}",
        conversation_id=conversation_id,
        principal=Principal(
            actor_id=actor_id,
            actor_type=ActorType.CUSTOMER,
            roles=["customer"],
            customer_id=customer_id,
        ),
        effective_customer_id=customer_id,
    )


def decision(
    intent: Intent,
    request_type: AgentRequestType,
    tool_name: str | None = None,
    arguments: dict[str, object] | None = None,
) -> StructuredDecision:
    return StructuredDecision(
        intent=intent,
        request_type=request_type,
        tool_name=tool_name,
        arguments=arguments or {},
        reason="checkpoint persistence test",
    )


def informational_decision() -> StructuredDecision:
    return decision(Intent.CAPABILITY_QUESTION, AgentRequestType.INFORMATIONAL)


def test_configured_serializer_round_trips_intentional_application_types(
    caplog: pytest.LogCaptureFixture,
) -> None:
    principal = Principal(
        actor_id="operator-checkpoint",
        actor_type=ActorType.SUPPORT_OPERATOR,
        roles=["support_operator"],
    )
    values = (
        ExecutionContext(
            request_id="request-checkpoint",
            conversation_id="conversation-checkpoint",
            principal=principal,
            effective_customer_id=2,
        ),
        principal,
        ActorType.SUPPORT_OPERATOR,
        Intent.ORDER_CANCEL,
        AgentRequestType.WRITE_ACTION,
        AgentErrorCategory.POLICY_DENIED,
        PolicyDecision(
            outcome=PolicyOutcome.REQUIRE_CONFIRMATION,
            tool_name="cancel_order",
            risk_level=2,
            reasons=["explicit confirmation required"],
        ),
        PolicyOutcome.REQUIRE_CONFIRMATION,
        PendingAction(
            action_id="act_checkpoint",
            conversation_id="conversation-checkpoint",
            actor_id=principal.actor_id,
            actor_type=principal.actor_type,
            effective_customer_id=2,
            tool_name="cancel_order",
            arguments={"customer_id": 2, "order_id": 3},
            risk_level=2,
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
        ),
        PendingActionStatus.PENDING,
        MemoryCandidate(
            memory_type=MemoryType.PREFERENCE,
            content="Prefers email updates",
            normalized_key="contact.preference",
        ),
        MemoryType.PREFERENCE,
    )
    serializer = build_checkpoint_serializer()

    with caplog.at_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus"):
        restored = [serializer.loads_typed(serializer.dumps_typed(value)) for value in values]

    assert [type(value) for value in restored] == [type(value) for value in values]
    assert restored == list(values)
    assert "Deserializing unregistered type" not in caplog.text
    assert serializer.pickle_fallback is False
    assert serializer._allowed_msgpack_modules == set(CHECKPOINT_ALLOWED_MSGPACK_TYPES)


def test_configured_serializer_rejects_unregistered_application_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    serializer = build_checkpoint_serializer()
    payload = serializer.dumps_typed(UnregisteredCheckpointType(value="must-not-load"))

    with (
        caplog.at_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus"),
        pytest.raises(CheckpointDeserializationError, match="UnregisteredCheckpointType"),
    ):
        serializer.loads_typed(payload)

    assert "Blocked deserialization of" in caplog.text
    assert "Deserializing unregistered type" not in caplog.text


def test_configured_serializer_reads_pre_strict_msgpack_checkpoint() -> None:
    legacy_serializer = JsonPlusSerializer()
    legacy_value = PolicyDecision(
        outcome=PolicyOutcome.REQUIRE_CONFIRMATION,
        tool_name="cancel_order",
        risk_level=2,
    )
    legacy_payload = legacy_serializer.dumps_typed(legacy_value)

    restored = build_checkpoint_serializer().loads_typed(legacy_payload)

    assert restored == legacy_value
    assert type(restored) is PolicyDecision


def test_checkpoint_allowlist_is_immutable_exact_application_symbols() -> None:
    assert len(CHECKPOINT_ALLOWED_MSGPACK_TYPES) == len(set(CHECKPOINT_ALLOWED_MSGPACK_TYPES))
    assert all(
        len(symbol) == 2 and symbol[0].startswith("app.")
        for symbol in CHECKPOINT_ALLOWED_MSGPACK_TYPES
    )


def test_thread_identity_contains_all_authoritative_scope_fields() -> None:
    context = execution_context(
        actor_id="customer:one",
        customer_id=17,
        conversation_id="shared:conversation",
    )

    thread_id = checkpoint_thread_id(context)

    assert thread_id == ("v1:customer:customer%3Aone:customer-17:shared%3Aconversation")
    assert checkpoint_thread_id_hash(context) == checkpoint_thread_id_hash(context)
    assert len(checkpoint_thread_id_hash(context)) == 16


def test_test_configuration_builds_an_in_memory_provider() -> None:
    provider = build_checkpoint_provider(Settings(checkpoint_backend="memory"))

    assert isinstance(provider, MemoryCheckpointProvider)
    assert provider.backend == CheckpointBackend.MEMORY


def test_same_thread_resumes_after_runtime_restart_simulation(db_session: Session) -> None:
    checkpoint_provider = MemoryCheckpointProvider()
    context = execution_context(conversation_id="restart-resume")
    first_provider = FakeDecisionProvider([informational_decision()])
    first_runtime = AgentRuntime(
        provider=first_provider,
        checkpointer=checkpoint_provider.checkpointer,
    )
    first_runtime.run(context=context, message="First request", session=db_session)

    second_provider = FakeDecisionProvider([informational_decision()])
    restarted_runtime = AgentRuntime(
        provider=second_provider,
        checkpointer=checkpoint_provider.checkpointer,
    )
    restarted_runtime.run(context=context, message="Second request", session=db_session)

    resumed_messages = [message["content"] for message in second_provider.calls[0]]
    assert resumed_messages[0] == "First request"
    assert resumed_messages[-1] == "Second request"


def test_pending_action_survives_restart_and_confirmation_executes(
    db_session: Session,
) -> None:
    checkpoint_provider = MemoryCheckpointProvider()
    context = execution_context(conversation_id="restart-confirmation")
    first_runtime = AgentRuntime(
        provider=FakeDecisionProvider(
            [
                decision(
                    Intent.ORDER_CANCEL,
                    AgentRequestType.WRITE_ACTION,
                    "cancel_order",
                    {"customer_id": 1, "order_id": 3},
                )
            ]
        ),
        checkpointer=checkpoint_provider.checkpointer,
    )
    pending = first_runtime.run(
        context=context,
        message="Cancel order 3",
        session=db_session,
    )

    restarted_runtime = AgentRuntime(
        provider=FakeDecisionProvider([]),
        checkpointer=checkpoint_provider.checkpointer,
    )
    confirmed = restarted_runtime.run(context=context, message="yes", session=db_session)

    order = db_session.get(Order, 3)
    assert pending.pending_action is not None
    assert pending.pending_action.status == PendingActionStatus.PENDING
    assert confirmed.agent_run_id != pending.agent_run_id
    assert confirmed.pending_action is not None
    assert confirmed.pending_action.status == PendingActionStatus.EXECUTED
    assert confirmed.pending_action.action_id == pending.pending_action.action_id
    assert order is not None
    assert OrderStatus(order.status) == OrderStatus.CANCELLED


def test_wrong_actor_cannot_confirm_action_after_restart(db_session: Session) -> None:
    checkpoint_provider = MemoryCheckpointProvider()
    owner_context = execution_context(
        actor_id="customer-owner",
        conversation_id="wrong-actor-confirmation",
    )
    first_runtime = AgentRuntime(
        provider=FakeDecisionProvider(
            [
                decision(
                    Intent.ORDER_CANCEL,
                    AgentRequestType.WRITE_ACTION,
                    "cancel_order",
                    {"customer_id": 1, "order_id": 3},
                )
            ]
        ),
        checkpointer=checkpoint_provider.checkpointer,
    )
    first_runtime.run(context=owner_context, message="Cancel order 3", session=db_session)

    wrong_actor_runtime = AgentRuntime(
        provider=FakeDecisionProvider([]),
        checkpointer=checkpoint_provider.checkpointer,
    )
    attempted = wrong_actor_runtime.run(
        context=execution_context(
            actor_id="customer-attacker",
            conversation_id="wrong-actor-confirmation",
        ),
        message="yes",
        session=db_session,
    )

    order = db_session.get(Order, 3)
    assert attempted.pending_action is None
    assert attempted.tool_call is None
    assert order is not None
    assert OrderStatus(order.status) == OrderStatus.PENDING


def test_different_actors_with_same_conversation_id_are_isolated(db_session: Session) -> None:
    checkpointer = MemorySaver()
    first_runtime = AgentRuntime(
        provider=FakeDecisionProvider([informational_decision()]),
        checkpointer=checkpointer,
    )
    first_runtime.run(
        context=execution_context(actor_id="actor-one", conversation_id="shared-conversation"),
        message="Actor one request",
        session=db_session,
    )
    second_provider = FakeDecisionProvider([informational_decision()])
    second_runtime = AgentRuntime(provider=second_provider, checkpointer=checkpointer)
    second_runtime.run(
        context=execution_context(actor_id="actor-two", conversation_id="shared-conversation"),
        message="Actor two request",
        session=db_session,
    )

    assert [message["content"] for message in second_provider.calls[0]] == ["Actor two request"]


def test_concurrent_customer_contexts_do_not_share_state(tmp_path: Path) -> None:
    # Each request owns its Session and connection, as in the API; the shared in-memory
    # fixture connection cannot hold two concurrent transactions.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add_all(
            [
                Customer(id=1, name="Test Customer", email="test@example.com"),
                Customer(id=2, name="Other Customer", email="other@example.com"),
            ]
        )
        seed.commit()
    checkpointer = MemorySaver()

    def run_customer(actor_id: str, customer_id: int) -> list[str]:
        provider = FakeDecisionProvider([informational_decision()])
        runtime = AgentRuntime(
            provider=provider,
            checkpointer=checkpointer,
            memory_service=MemoryService(enabled=False),
        )
        with Session(engine) as session:
            runtime.run(
                context=execution_context(
                    actor_id=actor_id,
                    customer_id=customer_id,
                    conversation_id="concurrent-conversation",
                ),
                message=f"Request from {actor_id}",
                session=session,
            )
        return [message["content"] for message in provider.calls[0]]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run_customer, "customer-one", 1)
        second = executor.submit(run_customer, "customer-two", 2)

    assert first.result() == ["Request from customer-one"]
    assert second.result() == ["Request from customer-two"]
