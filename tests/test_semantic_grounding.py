from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.decision_compiler import BusinessTargetResolver, CompileStatus, DecisionCompiler
from app.agent.llm.fake import FakeSemanticDecisionProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, SemanticDecision, SemanticTarget
from app.agent.semantic_grounding import GroundingStatus, validate_semantic_grounding
from app.auth.models import ActorType, Principal
from app.core.context import ExecutionContext
from app.tools.base import ResourceNotFoundError
from app.tools.orders import CancelOrderInput, validate_cancel_order


def _decision(intent: Intent, target: SemanticTarget | None) -> SemanticDecision:
    return SemanticDecision(
        intent=intent,
        request_type=AgentRequestType.WRITE_ACTION,
        target=target,
    )


def test_grounded_explicit_order_id_uses_current_user_message() -> None:
    result = validate_semantic_grounding(
        _decision(Intent.ORDER_CANCEL, SemanticTarget(type="explicit_order", order_id=3)),
        "Cancel order 3.",
    )
    assert result.status == GroundingStatus.GROUNDED
    assert result.trusted_source == "current_user_message"


def test_ungrounded_explicit_order_id_cannot_compile_destructive_action(
    db_session: Session,
) -> None:
    decision = _decision(
        Intent.ORDER_CANCEL,
        SemanticTarget(type="explicit_order", order_id=999),
    )
    grounding = validate_semantic_grounding(decision, "Cancel my order.")
    result = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        decision,
        _context(),
        grounding=grounding,
    )
    assert grounding.status == GroundingStatus.UNGROUNDED
    assert result.status == CompileStatus.CLARIFICATION_REQUIRED
    assert result.selected_tool is None
    assert result.tool_arguments == {}
    assert "999" not in result.reason


def test_explicit_fake_user_id_is_grounded_but_business_validation_rejects(
    db_session: Session,
) -> None:
    decision = _decision(
        Intent.ORDER_CANCEL,
        SemanticTarget(type="explicit_order", order_id=999),
    )
    grounding = validate_semantic_grounding(decision, "Cancel order 999.")
    result = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        decision,
        _context(),
        grounding=grounding,
    )
    assert grounding.status == GroundingStatus.GROUNDED
    assert result.status == CompileStatus.COMPILED_ACTION
    try:
        validate_cancel_order(
            db_session,
            CancelOrderInput.model_validate(result.tool_arguments),
        )
    except ResourceNotFoundError:
        pass
    else:
        raise AssertionError("A grounded but nonexistent order must fail business validation")


def test_latest_order_is_symbolic_and_not_grounded_against_literal_text() -> None:
    result = validate_semantic_grounding(
        _decision(Intent.ORDER_CANCEL, SemanticTarget(type="latest_order")),
        "Cancel my latest order.",
    )
    assert result.status == GroundingStatus.SYMBOLIC
    assert result.trusted_source is None


def test_model_cannot_replace_symbolic_latest_order_with_invented_id() -> None:
    result = validate_semantic_grounding(
        _decision(Intent.ORDER_CANCEL, SemanticTarget(type="explicit_order", order_id=123)),
        "Cancel my latest order.",
    )
    assert result.status == GroundingStatus.UNGROUNDED


def test_ungrounded_explicit_read_id_is_also_blocked() -> None:
    result = validate_semantic_grounding(
        _decision(Intent.ORDER_LOOKUP, SemanticTarget(type="explicit_order", order_id=123)),
        "Show me my order.",
    )
    assert result.status == GroundingStatus.UNGROUNDED


def test_runtime_clarifies_without_echoing_an_invented_id(db_session: Session) -> None:
    provider = FakeSemanticDecisionProvider(
        [
            _decision(
                Intent.ORDER_CANCEL,
                SemanticTarget(type="explicit_order", order_id=999),
            )
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        decision_contract_version="semantic_decision_v2",
    )
    response = runtime.run(
        conversation_id="grounding-runtime",
        customer_id=1,
        message="Cancel my order.",
        session=db_session,
    )
    assert response.pending_action is None
    assert response.tool_call is None
    assert "999" not in response.message


def _context() -> ExecutionContext:
    return ExecutionContext(
        request_id="grounding-request",
        conversation_id="grounding-conversation",
        principal=Principal(
            actor_id="grounding-test",
            actor_type=ActorType.SUPPORT_OPERATOR,
            roles=["support_operator"],
        ),
        effective_customer_id=1,
    )


_LISTING = "You have 2 orders: #1: delivered, $149.99; #2: shipped, $39.50."


def test_ordinal_read_resolves_against_the_listed_orders() -> None:
    lookup = _decision(Intent.ORDER_LOOKUP, SemanticTarget(type="explicit_order", order_id=2))
    assert (
        validate_semantic_grounding(lookup, "Where is the second one?", _LISTING).trusted_source
        == "previous_reply_listing"
    )
    assert validate_semantic_grounding(lookup, "İkincisi nerede?", _LISTING).status == (
        GroundingStatus.GROUNDED
    )
    wrong = _decision(Intent.ORDER_LOOKUP, SemanticTarget(type="explicit_order", order_id=1))
    assert validate_semantic_grounding(wrong, "Where is the second one?", _LISTING).status == (
        GroundingStatus.UNGROUNDED
    )
    assert validate_semantic_grounding(lookup, "Where is the second one?", "").status == (
        GroundingStatus.UNGROUNDED
    )


def test_ordinal_reference_never_grounds_a_write() -> None:
    cancel = _decision(Intent.ORDER_CANCEL, SemanticTarget(type="explicit_order", order_id=2))
    assert validate_semantic_grounding(cancel, "Cancel the second one.", _LISTING).status == (
        GroundingStatus.UNGROUNDED
    )
