from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeSemanticDecisionV3Provider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, SemanticDecisionV3
from app.models import Order, RefundRequest
from app.models.entities import OrderStatus
from app.policies.models import PendingActionStatus
from app.rag.interfaces import RetrievalMetadata, RetrievalResult
from app.rag.schemas import RetrievedChunk
from app.ui.repository import InMemoryAgentRunProjectionRepository


class FixedKnowledgeRetriever:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, query: str) -> RetrievalResult:
        self.queries.append(query)
        chunks: Sequence[RetrievedChunk] = (
            RetrievedChunk(
                chunk_id="refund-policy#eligibility#0",
                document_id="refund-policy",
                title="Refund Policy",
                category="refund",
                section="eligibility",
                source="knowledge/refund-policy.md",
                content=(
                    "Delivered damaged products may be submitted for refund review after "
                    "verification and customer confirmation."
                ),
                score=1.0,
            ),
        )
        return RetrievalResult(
            chunks=tuple(chunks),
            metadata=RetrievalMetadata(
                backend="test",
                embedding_provider="deterministic",
                reranker_enabled=False,
                retrieval_count=1,
                latency_seconds=0.0,
            ),
        )


def _refund_decision(*, order_id: int | None = 2) -> SemanticDecisionV3:
    target = {"type": "explicit_order", "order_id": order_id} if order_id is not None else None
    return SemanticDecisionV3(
        intent=Intent.REFUND_REQUEST,
        request_type=AgentRequestType.WRITE_ACTION,
        target=target,
        reason="damaged product",
    )


def _refund_policy_decision() -> SemanticDecisionV3:
    return SemanticDecisionV3(
        intent=Intent.REFUND_POLICY,
        request_type=AgentRequestType.KNOWLEDGE_ONLY,
        requires_retrieval=True,
        knowledge_query="refund policy for damaged products",
    )


def _support_faq_decision() -> SemanticDecisionV3:
    return SemanticDecisionV3(
        intent=Intent.SUPPORT_FAQ,
        request_type=AgentRequestType.KNOWLEDGE_ONLY,
        requires_retrieval=True,
        knowledge_query="support contact options",
    )


def test_refund_confirmation_workflow_suspends_answers_and_resumes(
    db_session: Session,
) -> None:
    provider = FakeSemanticDecisionV3Provider([_refund_decision(), _refund_policy_decision()])
    retriever = FixedKnowledgeRetriever()
    projections = InMemoryAgentRunProjectionRepository()
    runtime = AgentRuntime(
        provider=provider,
        knowledge_retriever=retriever,
        projection_repository=projections,
    )

    pending = runtime.run(
        conversation_id="refund-interruption",
        customer_id=1,
        message="Refund order 2 because it is a damaged product.",
        session=db_session,
    )
    interrupted = runtime.run(
        conversation_id="refund-interruption",
        customer_id=1,
        message="What is your refund policy?",
        session=db_session,
    )

    assert pending.pending_action is not None
    assert pending.pending_action.status == PendingActionStatus.PENDING
    assert "request_refund" not in pending.message
    assert "submit your refund request" in pending.message
    assert interrupted.intent == Intent.REFUND_POLICY
    assert interrupted.pending_action is None
    assert interrupted.tool_call is None
    assert interrupted.citations
    assert "I can answer that first." in interrupted.message
    assert "waiting for confirmation" in interrupted.message
    assert db_session.scalar(select(RefundRequest)) is None
    interrupted_view = projections.get_by_run_id(interrupted.agent_run_id)
    assert interrupted_view is not None
    suspension = next(
        event for event in interrupted_view.trace if event.name == "handle_workflow_interruption"
    )
    assert suspension.metadata["workflow_state"] == "suspended"
    assert suspension.metadata["workflow_transition"] == ("waiting_confirmation_to_suspended")
    assert suspension.metadata["previous_workflow_intent"] == "refund_request"
    assert suspension.metadata["interruption_intent"] == "refund_policy"
    assert suspension.metadata["interruption_type"] == "temporary_request"
    assert suspension.metadata["superseded_by"] == "not_applicable"

    resumed = runtime.run(
        conversation_id="refund-interruption",
        customer_id=1,
        message="Continue with my refund.",
        session=db_session,
    )
    assert resumed.pending_action is not None
    assert resumed.pending_action.action_id == pending.pending_action.action_id
    assert resumed.pending_action.status == PendingActionStatus.PENDING
    assert resumed.tool_call is None
    assert resumed.pending_action.status == PendingActionStatus.PENDING
    assert len(provider.calls) == 2
    resumed_view = projections.get_by_run_id(resumed.agent_run_id)
    assert resumed_view is not None
    resume_event = next(
        event for event in resumed_view.trace if event.name == "restore_suspended_workflow"
    )
    assert resume_event.metadata["workflow_state"] == "resumed"
    assert resume_event.metadata["previous_workflow_intent"] == "refund_request"
    assert resume_event.metadata["resume_source"] == "explicit_user_resume"
    assert int(resume_event.metadata["restored_fields_count"]) >= 6

    executed = runtime.run(
        conversation_id="refund-interruption",
        customer_id=1,
        message="Yes, please proceed.",
        session=db_session,
    )
    assert executed.tool_call is not None
    assert executed.tool_call.name == "request_refund"
    assert executed.tool_call.status == "executed"
    assert db_session.scalar(select(RefundRequest).where(RefundRequest.order_id == 2)) is not None


def test_cancellation_interruption_preserves_confirmation_and_rejection_is_safe(
    db_session: Session,
) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.ORDER_CANCEL,
                request_type=AgentRequestType.WRITE_ACTION,
                target={"type": "explicit_order", "order_id": 3},
            ),
            _support_faq_decision(),
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        knowledge_retriever=FixedKnowledgeRetriever(),
    )

    pending = runtime.run(
        conversation_id="cancel-interruption",
        customer_id=1,
        message="Cancel order 3.",
        session=db_session,
    )
    interrupted = runtime.run(
        conversation_id="cancel-interruption",
        customer_id=1,
        message="Yes, but first explain how I can contact support.",
        session=db_session,
    )
    order = db_session.get(Order, 3)
    assert pending.pending_action is not None
    assert interrupted.intent == Intent.SUPPORT_FAQ
    assert interrupted.pending_action is None
    assert interrupted.tool_call is None
    assert "I can answer that first." in interrupted.message
    assert "waiting for confirmation" in interrupted.message
    assert order is not None
    assert OrderStatus(order.status) == OrderStatus.PENDING

    resumed = runtime.run(
        conversation_id="cancel-interruption",
        customer_id=1,
        message="Resume my cancellation.",
        session=db_session,
    )
    rejected = runtime.run(
        conversation_id="cancel-interruption",
        customer_id=1,
        message="No, cancel it.",
        session=db_session,
    )
    assert resumed.pending_action is not None
    assert resumed.pending_action.status == PendingActionStatus.PENDING
    assert rejected.pending_action is not None
    assert rejected.pending_action.status == PendingActionStatus.REJECTED
    assert rejected.tool_call is None
    db_session.refresh(order)
    assert OrderStatus(order.status) == OrderStatus.PENDING


def test_incomplete_refund_workflow_is_restored_after_policy_question(
    db_session: Session,
) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [_refund_decision(order_id=None), _refund_policy_decision()]
    )
    runtime = AgentRuntime(
        provider=provider,
        knowledge_retriever=FixedKnowledgeRetriever(),
    )

    initial = runtime.run(
        conversation_id="incomplete-refund-interruption",
        customer_id=1,
        message="I received a damaged product and want a refund.",
        session=db_session,
    )
    interrupted = runtime.run(
        conversation_id="incomplete-refund-interruption",
        customer_id=1,
        message="What is your refund policy?",
        session=db_session,
    )
    resumed = runtime.run(
        conversation_id="incomplete-refund-interruption",
        customer_id=1,
        message="Continue with my refund.",
        session=db_session,
    )
    continued = runtime.run(
        conversation_id="incomplete-refund-interruption",
        customer_id=1,
        message="2",
        session=db_session,
    )

    assert "order number" in initial.message.casefold()
    assert interrupted.intent == Intent.REFUND_POLICY
    assert interrupted.pending_action is None
    assert resumed.intent == Intent.REFUND_REQUEST
    assert "order number" in resumed.message.casefold()
    assert continued.pending_action is not None
    assert continued.pending_action.arguments["order_id"] == 2
    assert continued.pending_action.arguments["reason"] == "damaged product"
    assert len(provider.calls) == 2


def test_unrelated_question_without_workflow_uses_normal_path(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider([_refund_policy_decision()])
    runtime = AgentRuntime(
        provider=provider,
        knowledge_retriever=FixedKnowledgeRetriever(),
    )

    result = runtime.run(
        conversation_id="no-workflow-question",
        customer_id=1,
        message="What is your refund policy?",
        session=db_session,
    )

    assert result.intent == Intent.REFUND_POLICY
    assert result.pending_action is None
    assert result.tool_call is None
    assert result.citations
    assert len(provider.calls) == 1


def test_resume_command_without_suspended_workflow_does_not_classify_or_execute(
    db_session: Session,
) -> None:
    provider = FakeSemanticDecisionV3Provider([])
    runtime = AgentRuntime(provider=provider)

    result = runtime.run(
        conversation_id="resume-without-workflow",
        customer_id=1,
        message="Continue",
        session=db_session,
    )

    assert result.message == "There is no suspended request to continue."
    assert result.pending_action is None
    assert result.tool_call is None
    assert provider.calls == []


def test_confirmation_like_question_suspends_instead_of_confirming(
    db_session: Session,
) -> None:
    provider = FakeSemanticDecisionV3Provider([_refund_decision(), _refund_policy_decision()])
    projections = InMemoryAgentRunProjectionRepository()
    runtime = AgentRuntime(
        provider=provider,
        knowledge_retriever=FixedKnowledgeRetriever(),
        projection_repository=projections,
    )

    pending = runtime.run(
        conversation_id="mixed-confirmation-question",
        customer_id=1,
        message="Refund order 2 because it is a damaged product.",
        session=db_session,
    )
    interrupted = runtime.run(
        conversation_id="mixed-confirmation-question",
        customer_id=1,
        message="Yes, but first what is your refund policy?",
        session=db_session,
    )

    assert pending.pending_action is not None
    assert interrupted.intent == Intent.REFUND_POLICY
    assert interrupted.pending_action is None
    assert interrupted.tool_call is None
    assert interrupted.citations
    assert db_session.scalar(select(RefundRequest).where(RefundRequest.order_id == 2)) is None
    interrupted_view = projections.get_by_run_id(interrupted.agent_run_id)
    assert interrupted_view is not None
    suspension = next(
        event for event in interrupted_view.trace if event.name == "handle_workflow_interruption"
    )
    assert suspension.metadata["workflow_state"] == "suspended"
    confirmation = next(
        event for event in interrupted_view.trace if event.name == "check_pending_action"
    )
    assert confirmation.metadata["confirmation_result"] == "inspect_interruption"


def test_knowledge_interruption_ignores_carried_forward_target(
    db_session: Session,
) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [
            _refund_decision(),
            _refund_policy_decision().model_copy(
                update={
                    "request_type": AgentRequestType.WRITE_ACTION,
                    "target": {"type": "explicit_order", "order_id": 2},
                }
            ),
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        knowledge_retriever=FixedKnowledgeRetriever(),
    )

    pending = runtime.run(
        conversation_id="carried-target-interruption",
        customer_id=1,
        message="Refund order 2 because it is a damaged product.",
        session=db_session,
    )
    interrupted = runtime.run(
        conversation_id="carried-target-interruption",
        customer_id=1,
        message="Yes, but first explain refund policy.",
        session=db_session,
    )

    assert pending.pending_action is not None
    assert interrupted.intent == Intent.REFUND_POLICY
    assert interrupted.citations
    assert interrupted.tool_call is None
    assert "I can answer that first." in interrupted.message
    assert "waiting for confirmation" in interrupted.message


def test_explicit_cancel_request_supersedes_pending_refund(db_session: Session) -> None:
    # Eligibility is checked before confirmation, so both targets must be eligible.
    db_session.add(Order(id=4115, customer_id=1, status=OrderStatus.PENDING, total_amount="19.00"))
    db_session.add(
        Order(id=4116, customer_id=1, status=OrderStatus.DELIVERED, total_amount="29.00")
    )
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            _refund_decision(order_id=4116),
            SemanticDecisionV3(
                intent=Intent.ORDER_CANCEL,
                request_type=AgentRequestType.WRITE_ACTION,
                target={"type": "explicit_order", "order_id": 4115},
            ),
        ]
    )
    projections = InMemoryAgentRunProjectionRepository()
    runtime = AgentRuntime(provider=provider, projection_repository=projections)

    refund = runtime.run(
        conversation_id="supersede-refund",
        customer_id=1,
        message="Refund order 4116 because it is a damaged product.",
        session=db_session,
    )
    cancellation = runtime.run(
        conversation_id="supersede-refund",
        customer_id=1,
        message="Actually cancel my order 4115 instead.",
        session=db_session,
    )

    assert refund.pending_action is not None
    assert cancellation.intent == Intent.ORDER_CANCEL
    assert cancellation.pending_action is not None
    assert cancellation.pending_action.tool_name == "cancel_order"
    # The order named in the replacement wins over the one the refund was about.
    assert cancellation.pending_action.arguments["order_id"] == 4115
    assert cancellation.pending_action.action_id != refund.pending_action.action_id
    assert cancellation.pending_action.status == PendingActionStatus.PENDING
    assert cancellation.tool_call is None
    view = projections.get_by_run_id(cancellation.agent_run_id)
    assert view is not None
    event = next(item for item in view.trace if item.name == "handle_workflow_interruption")
    assert event.event_key == "workflow.superseded"
    assert event.metadata["workflow_state"] == "superseded"
    assert event.metadata["workflow_transition"] == ("waiting_confirmation_to_superseded")
    assert str(event.metadata["previous_workflow"]).startswith("workflow:")
    assert event.metadata["previous_workflow"] != event.metadata["new_workflow"]
    assert event.metadata["new_workflow"] == event.metadata["superseded_by"]


def test_cancel_pending_is_symmetrically_replaced_by_refund(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.ORDER_CANCEL,
                request_type=AgentRequestType.WRITE_ACTION,
                target={"type": "explicit_order", "order_id": 3},
            )
        ]
    )
    runtime = AgentRuntime(provider=provider)

    cancellation = runtime.run(
        conversation_id="supersede-cancel",
        customer_id=1,
        message="Cancel order 3.",
        session=db_session,
    )
    refund = runtime.run(
        conversation_id="supersede-cancel",
        customer_id=1,
        message="No, let's refund it instead.",
        session=db_session,
    )

    assert cancellation.pending_action is not None
    assert refund.intent == Intent.REFUND_REQUEST
    assert refund.pending_action is None
    assert "reason" in refund.message.casefold() or "why" in refund.message.casefold()
    assert len(provider.calls) == 1


def test_bounded_confirmation_still_executes_after_resume(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider([_refund_decision(), _refund_policy_decision()])
    runtime = AgentRuntime(
        provider=provider,
        knowledge_retriever=FixedKnowledgeRetriever(),
    )

    runtime.run(
        conversation_id="resume-then-confirm",
        customer_id=1,
        message="Refund order 2 because it is a damaged product.",
        session=db_session,
    )
    runtime.run(
        conversation_id="resume-then-confirm",
        customer_id=1,
        message="What is your refund policy?",
        session=db_session,
    )
    resumed = runtime.run(
        conversation_id="resume-then-confirm",
        customer_id=1,
        message="Proceed",
        session=db_session,
    )
    confirmed = runtime.run(
        conversation_id="resume-then-confirm",
        customer_id=1,
        message="Yes, proceed",
        session=db_session,
    )

    assert resumed.pending_action is not None
    assert resumed.pending_action.status == PendingActionStatus.PENDING
    assert resumed.tool_call is None
    assert confirmed.tool_call is not None
    assert confirmed.tool_call.name == "request_refund"
    assert confirmed.tool_call.status == "executed"


def test_yes_after_a_side_question_restores_but_never_confirms(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider([_refund_decision(), _refund_policy_decision()])
    runtime = AgentRuntime(provider=provider, knowledge_retriever=FixedKnowledgeRetriever())

    def say(message: str):
        return runtime.run(
            conversation_id="yes-after-question",
            customer_id=1,
            message=message,
            session=db_session,
        )

    pending = say("Refund order 2 because it is a damaged product.")
    say("What is your refund policy?")
    restored = say("Okay, yes, refund it.")

    assert pending.pending_action is not None
    assert restored.pending_action is not None
    assert restored.pending_action.action_id == pending.pending_action.action_id
    assert restored.pending_action.status == PendingActionStatus.PENDING
    assert restored.tool_call is None
    assert db_session.scalar(select(RefundRequest)) is None
    assert len(provider.calls) == 2
