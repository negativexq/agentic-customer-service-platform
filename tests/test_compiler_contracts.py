from typing import Any

import pytest
from pydantic import ValidationError, create_model
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.compiler_contracts import CompilerField, CompilerReasonCode
from app.agent.decision_compiler import (
    ACTION_TOOLS,
    COMPILER_REASON_STATUSES,
    READ_TOOLS,
    SEMANTIC_INTENT_ROUTES,
    BusinessTargetResolver,
    CompiledDecision,
    CompileStatus,
    DecisionCompiler,
)
from app.agent.llm.fake import FakeSemanticDecisionV3Provider
from app.agent.nodes.compile_decision import make_compile_decision_node
from app.agent.nodes.resume_workflow import make_resume_workflow_node
from app.agent.runtime import AgentRuntime
from app.agent.schemas import (
    AgentRequestType,
    AgentResponse,
    Intent,
    SemanticDecision,
    SemanticDecisionV3,
)
from app.agent.semantic_grounding import GroundingStatus, SemanticGrounding
from app.agent.state import AgentState
from app.api.routes.ui import _bounded_operator_view
from app.auth.models import ActorType, Principal
from app.core.context import ExecutionContext
from app.models import RefundRequest
from app.persistence.checkpoint import MemoryCheckpointProvider, build_checkpoint_serializer
from app.policies.models import PendingActionStatus
from app.ui.projection import get_projection_store
from app.ui.repository import SqlAlchemyAgentRunProjectionRepository
from app.ui.schemas import UICompilerDecision

ORDER = {"type": "explicit_order", "order_id": 2}
TICKET = {"type": "explicit_ticket", "ticket_id": 1}


def proposal(intent: Intent, **fields: object) -> SemanticDecision:
    return SemanticDecision.model_validate({"intent": intent, **fields})


def context() -> ExecutionContext:
    return ExecutionContext(
        request_id="compiler-contract-request",
        conversation_id="compiler-contract-thread",
        principal=Principal(
            actor_id="compiler-contract-operator",
            actor_type=ActorType.SUPPORT_OPERATOR,
            roles=["support_operator"],
        ),
        effective_customer_id=1,
    )


@pytest.mark.parametrize(
    "decision,message,code,missing,clarify",
    [
        (
            proposal(Intent.REFUND_REQUEST),
            "Refund my order",
            "target_required",
            ["order_id"],
            ["order_id"],
        ),
        (
            proposal(Intent.ORDER_LOOKUP),
            "Check my order",
            "order_required",
            ["order_id"],
            ["order_id"],
        ),
        (
            proposal(Intent.REFUND_REQUEST, target=ORDER),
            "Refund order 2",
            "refund_reason_required",
            ["reason"],
            ["reason"],
        ),
        (
            proposal(Intent.REFUND_REQUEST, target=ORDER, reason="broken"),
            "Refund order 2",
            "refund_reason_unsupported",
            [],
            ["reason"],
        ),
        (
            proposal(Intent.TICKET_CREATE, description="A delivery issue"),
            "Create a ticket",
            "ticket_details_required",
            ["category"],
            ["category"],
        ),
        (
            proposal(Intent.TICKET_CREATE, category="delivery"),
            "Create a ticket",
            "ticket_details_required",
            ["description"],
            ["description"],
        ),
        (
            proposal(Intent.TICKET_CREATE),
            "Create a ticket",
            "ticket_details_required",
            ["category", "description"],
            ["category", "description"],
        ),
        (
            proposal(Intent.TICKET_CREATE, category="delivery", description="Late"),
            "Order is late",
            "ticket_request_required",
            [],
            [],
        ),
        (
            proposal(Intent.HUMAN_ESCALATION, reason="need help"),
            "Escalate",
            "escalation_details_required",
            ["priority", "summary"],
            ["priority", "summary"],
        ),
        (
            proposal(Intent.HUMAN_ESCALATION, priority="high", summary="Assistance"),
            "Escalate",
            "escalation_details_required",
            ["reason"],
            ["reason"],
        ),
        (
            proposal(Intent.TICKET_LOOKUP),
            "Check my ticket",
            "ticket_required",
            ["ticket_id"],
            ["ticket_id"],
        ),
        (
            proposal(Intent.ORDER_LOOKUP, target={"type": "latest_order"}),
            "Show an order",
            "latest_order_not_grounded",
            ["order_id"],
            ["order_id"],
        ),
        (
            proposal(Intent.ORDER_CANCEL, target=ORDER),
            "Do not cancel order 2",
            "contradictory_action",
            [],
            [],
        ),
        (
            proposal(Intent.REFUND_POLICY, clarification_required=True),
            "Policy?",
            "knowledge_query_required",
            ["knowledge_query"],
            ["knowledge_query"],
        ),
        (
            proposal(Intent.MEMORY_REMEMBER),
            "Remember",
            "memory_candidate_required",
            ["memory_candidate"],
            ["memory_candidate"],
        ),
        (
            proposal(Intent.MEMORY_FORGET),
            "Forget",
            "memory_key_required",
            ["memory_key"],
            ["memory_key"],
        ),
        (proposal(Intent.UNKNOWN), "Hello?", "request_clarification_required", [], []),
    ],
)
def test_clarification_metadata_comes_from_typed_inputs(
    db_session: Session,
    decision: SemanticDecision,
    message: str,
    code: str,
    missing: list[str],
    clarify: list[str],
) -> None:
    result = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        decision, context(), user_message=message
    )
    assert result.status == CompileStatus.CLARIFICATION_REQUIRED
    assert result.reason_code == code
    assert result.missing_fields == missing
    assert result.clarification_fields == clarify
    assert result.selected_tool is None and result.tool_arguments == {}
    assert all(isinstance(field, CompilerField) for field in result.clarification_fields)


@pytest.mark.parametrize(
    "decision,message,status,code",
    [
        (
            proposal(Intent.REFUND_REQUEST, target=ORDER, reason="damaged"),
            "Refund order 2: damaged",
            CompileStatus.COMPILED_ACTION,
            "compiled_action",
        ),
        (
            proposal(Intent.CUSTOMER_LOOKUP),
            "My details",
            CompileStatus.COMPILED_ACTION,
            "compiled_action",
        ),
        (
            proposal(Intent.REFUND_ELIGIBILITY, target=ORDER),
            "Is order 2 eligible?",
            CompileStatus.COMPILED_ACTION,
            "compiled_action",
        ),
        (
            proposal(
                Intent.REFUND_POLICY, knowledge_query="refund policy", requires_retrieval=True
            ),
            "Refund policy",
            CompileStatus.NO_ACTION,
            "knowledge_only",
        ),
        (
            proposal(Intent.MEMORY_FORGET, memory_key="contact_channel"),
            "Forget email",
            CompileStatus.NO_ACTION,
            "memory_action",
        ),
        (
            proposal(Intent.ORDER_CANCEL, target=TICKET),
            "Cancel ticket 1",
            CompileStatus.COMPILE_REJECTED,
            "wrong_target_type",
        ),
        (
            proposal(Intent.CUSTOMER_LOOKUP, target=ORDER),
            "My details",
            CompileStatus.COMPILE_REJECTED,
            "target_not_allowed",
        ),
        (
            proposal(Intent.HUMAN_ESCALATION, reason="help", priority="untrusted", summary="Help"),
            "Escalate",
            CompileStatus.COMPILE_REJECTED,
            "invalid_escalation_priority",
        ),
    ],
)
def test_all_outcome_states_have_machine_codes_and_no_spurious_fields(
    db_session: Session, decision: SemanticDecision, message: str, status: CompileStatus, code: str
) -> None:
    result = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        decision, context(), user_message=message
    )
    assert result.status == status and result.reason_code == code
    assert result.missing_fields == result.clarification_fields == []
    if status != CompileStatus.COMPILED_ACTION:
        assert result.selected_tool is None and result.tool_arguments == {}


def test_provided_but_ungrounded_target_is_clarified_not_reported_missing(
    db_session: Session,
) -> None:
    result = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        proposal(Intent.REFUND_REQUEST, target=ORDER, reason="damaged"),
        context(),
        grounding=SemanticGrounding(status=GroundingStatus.UNGROUNDED),
        user_message="Refund order 3: damaged",
    )
    assert result.reason_code == CompilerReasonCode.TARGET_REQUIRED
    assert result.missing_fields == []
    assert result.clarification_fields == [CompilerField.ORDER_ID]
    assert result.selected_tool is None


def test_unresolved_symbolic_target_has_explicit_metadata(db_session: Session) -> None:
    result = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        proposal(
            Intent.TICKET_CREATE,
            target={"type": "latest_order"},
            category="delivery",
            description="Late",
        ),
        context().model_copy(update={"effective_customer_id": 999}),
        user_message="Create a ticket about my latest order",
    )
    assert result.reason_code == CompilerReasonCode.ORDER_UNRESOLVED
    assert result.missing_fields == result.clarification_fields == [CompilerField.ORDER_ID]


@pytest.mark.parametrize(
    "route,code",
    [(None, "unrouted_intent"), ("action", "unsupported_action"), ("read", "unsupported_read")],
)
def test_unhandled_registry_routes_fail_closed_with_codes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, route: str | None, code: str
) -> None:
    if route is None:
        monkeypatch.delitem(SEMANTIC_INTENT_ROUTES, Intent.UNKNOWN)
    else:
        monkeypatch.setitem(SEMANTIC_INTENT_ROUTES, Intent.UNKNOWN, route)
        monkeypatch.setitem(
            ACTION_TOOLS if route == "action" else READ_TOOLS, Intent.UNKNOWN, "unregistered"
        )
    result = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        proposal(Intent.UNKNOWN), context()
    )
    assert result.status == CompileStatus.COMPILE_REJECTED
    assert result.reason_code == code
    assert result.selected_tool is None and not result.tool_arguments


def test_reason_catalog_is_exhaustive_and_not_provider_owned() -> None:
    assert set(COMPILER_REASON_STATUSES) == set(CompilerReasonCode) - {
        CompilerReasonCode.NOT_RECORDED
    }
    for schema in (SemanticDecision, SemanticDecisionV3):
        for field in ("reason_code", "missing_fields", "clarification_fields"):
            assert field not in schema.model_json_schema()["properties"]
            with pytest.raises(ValidationError):
                schema.model_validate({"intent": "unknown", field: "untrusted"})


@pytest.mark.parametrize(
    "changes",
    [
        {"reason_code": "untrusted"},
        {"missing_fields": ["customer_id"]},
        {"clarification_fields": ["reason", "reason"]},
        {"missing_fields": ["reason"], "clarification_fields": []},
        {"status": "no_action"},
        {"selected_tool": "request_refund"},
        {"tool_arguments": {"order_id": 2}},
        {"missing_required_fields": ["reason"]},  # Ambiguous old/new field names fail closed.
    ],
)
def test_inconsistent_machine_contracts_are_rejected(changes: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "intent": "refund_request",
        "request_type": "unclear",
        "status": "clarification_required",
        "reason_code": "refund_reason_required",
        "missing_fields": ["reason"],
        "clarification_fields": ["reason"],
    }
    with pytest.raises(ValidationError):
        CompiledDecision.model_validate({**payload, **changes})


def reword_compiler(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    original = DecisionCompiler._clarification

    def reword(decision: SemanticDecision, reason: str, **kwargs: Any) -> CompiledDecision:
        return original(decision, text, **kwargs)

    monkeypatch.setattr(DecisionCompiler, "_clarification", staticmethod(reword))


@pytest.mark.parametrize(
    "copy",
    [
        "Bilgi eksik.",
        "More information, please.",
        "specific ticket authorized target category and description",
    ],
)
def test_wording_cannot_change_workflow_fields_or_resumption(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, copy: str
) -> None:
    reword_compiler(monkeypatch, copy)
    state: AgentState = {
        "execution_context": context(),
        "agent_run_id": "compiler-copy-run",
        "semantic_decision": proposal(Intent.REFUND_REQUEST, target=ORDER),
        "messages": [{"role": "user", "content": "Refund order 2"}],
    }
    node = make_compile_decision_node(db_session)
    initial = node(state)
    assert initial["workflow_active"] is True and initial["missing_required_fields"] == ["reason"]
    compiled = initial["compile_result"]
    assert compiled is not None and compiled.reason == copy
    assert compiled.reason_code == CompilerReasonCode.REFUND_REASON_REQUIRED
    state.update(initial)
    state["messages"] += [{"role": "user", "content": "The item arrived damaged"}]
    state.update(make_resume_workflow_node()(state))
    completed = node(state)
    assert completed["selected_tool"] == "request_refund"
    assert completed["tool_arguments"] == {
        "customer_id": 1,
        "order_id": 2,
        "reason": "The item arrived damaged",
    }
    assert not completed["workflow_active"] and completed["missing_required_fields"] == []
    assert db_session.scalar(select(func.count()).select_from(RefundRequest)) == 0


def test_strict_checkpoint_round_trip_and_legacy_field_adapter(db_session: Session) -> None:
    serializer = build_checkpoint_serializer()
    current = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        proposal(Intent.REFUND_REQUEST, target=ORDER), context(), user_message="Refund order 2"
    )
    restored = serializer.loads_typed(serializer.dumps_typed(current))
    assert isinstance(restored, CompiledDecision)
    assert restored.model_dump() == current.model_dump()
    legacy_model = create_model(
        "CompiledDecision",
        __module__=CompiledDecision.__module__,
        status=(CompileStatus, ...),
        intent=(Intent, ...),
        request_type=(AgentRequestType, ...),
        reason=(str, ""),
        missing_required_fields=(list[str], ...),
    )
    legacy = legacy_model(
        status="clarification_required",
        intent="refund_request",
        request_type="unclear",
        reason="An obsolete localized explanation",
        missing_required_fields=["reason"],
    )
    loaded = serializer.loads_typed(serializer.dumps_typed(legacy))
    assert isinstance(loaded, CompiledDecision)
    assert loaded.reason_code == CompilerReasonCode.NOT_RECORDED
    assert loaded.missing_fields == []  # Historical absence is unknown; never guessed from copy.
    assert loaded.clarification_fields == [CompilerField.REASON]
    assert "missing_required_fields" not in loaded.model_dump()
    resume = make_resume_workflow_node()(
        {
            "compile_result": loaded,
            "workflow_active": True,
            "missing_required_fields": ["reason"],
            "pending_workflow_decision": proposal(Intent.REFUND_REQUEST, target=ORDER),
            "messages": [{"role": "user", "content": "damaged"}],
        }
    )
    assert resume["workflow_resume_status"] == "resumed"


def test_runtime_rewording_and_restart_keep_confirmation_and_one_effect(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    reword_compiler(monkeypatch, "İade için ek bilgi gerekli.")
    checkpoint = MemoryCheckpointProvider().checkpointer
    provider = FakeSemanticDecisionV3Provider(
        [SemanticDecisionV3.model_validate({"intent": "refund_request", "target": ORDER})]
    )
    first_runtime = AgentRuntime(
        provider=provider, decision_contract_version="semantic_decision_v3", checkpointer=checkpoint
    )
    first = first_runtime.run(
        conversation_id="contract-restart",
        customer_id=1,
        message="Refund order 2",
        session=db_session,
    )
    assert first.pending_action is None
    assert db_session.scalar(select(func.count()).select_from(RefundRequest)) == 0
    restarted = AgentRuntime(
        provider=provider, decision_contract_version="semantic_decision_v3", checkpointer=checkpoint
    )
    pending = restarted.run(
        conversation_id="contract-restart",
        customer_id=1,
        message="The product arrived damaged",
        session=db_session,
    )
    assert (
        pending.pending_action is not None
        and pending.pending_action.status == PendingActionStatus.PENDING
    )
    assert db_session.scalar(select(func.count()).select_from(RefundRequest)) == 0
    confirmed = restarted.run(
        conversation_id="contract-restart", customer_id=1, message="confirm", session=db_session
    )
    replay = restarted.run(
        conversation_id="contract-restart", customer_id=1, message="confirm", session=db_session
    )
    assert (
        confirmed.pending_action is not None
        and confirmed.pending_action.status == PendingActionStatus.EXECUTED
    )
    assert (
        replay.pending_action is not None
        and replay.pending_action.action_id == confirmed.pending_action.action_id
    )
    assert db_session.scalar(select(func.count()).select_from(RefundRequest)) == 1
    assert len(provider.calls) == 1


def test_projection_and_persistence_preserve_machine_fields_and_old_views(
    db_session: Session,
) -> None:
    compiled = DecisionCompiler(BusinessTargetResolver(db_session)).compile(
        proposal(Intent.REFUND_REQUEST, target=ORDER, reason="invented"),
        context(),
        user_message="Refund order 2",
    )
    compiled.reason = "A different display explanation."
    store = get_projection_store()
    response = AgentResponse(
        conversation_id=context().conversation_id,
        agent_run_id="contract-projection",
        message=compiled.reason,
        intent=compiled.intent,
        request_type=compiled.request_type,
    )
    state: AgentState = {"compile_result": compiled, "request_type": compiled.request_type}
    with store.capture(
        run_id=response.agent_run_id, context=context(), trace_id="contract-trace"
    ) as projection:
        view = store.build_view(
            projection, response=response, state=state, policy_events=[], duration_ms=1.0
        )
    bounded = _bounded_operator_view(view).evidence.compiler
    assert bounded.reason_code == CompilerReasonCode.REFUND_REASON_UNSUPPORTED
    assert bounded.missing_fields == [] and bounded.clarification_fields == [CompilerField.REASON]
    assert bounded.reason == compiled.reason
    repo = SqlAlchemyAgentRunProjectionRepository(db_session)
    repo.upsert(view)
    db_session.expire_all()
    loaded = repo.get_by_run_id(view.run_id)
    assert loaded is not None and loaded.evidence.compiler == bounded
    old = UICompilerDecision.model_validate(
        {"status": "clarification_required", "reason": "A refund reason is required."}
    )
    assert old.reason_code == CompilerReasonCode.NOT_RECORDED
    assert old.missing_fields == old.clarification_fields == []
