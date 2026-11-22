from collections.abc import Callable
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from app.agent.decision_compiler import CompileStatus
from app.agent.llm.base import DecisionProposalProvider
from app.agent.nodes.check_pending import make_check_pending_node
from app.agent.nodes.compile_decision import make_compile_decision_node
from app.agent.nodes.confirmed import make_confirmed_execution_node
from app.agent.nodes.create_pending import make_create_pending_node
from app.agent.nodes.evaluate_policy import make_evaluate_policy_node
from app.agent.nodes.human import make_human_escalation_node
from app.agent.nodes.memory_action import make_memory_action_node
from app.agent.nodes.respond import respond
from app.agent.nodes.restore_pending import make_restore_pending_node
from app.agent.nodes.resume_workflow import make_resume_workflow_node
from app.agent.nodes.retrieve_knowledge import make_retrieve_node
from app.agent.nodes.retrieve_memory import make_retrieve_memory_node
from app.agent.nodes.revalidate import make_revalidate_node
from app.agent.nodes.select_tool import select_tool
from app.agent.nodes.understand_request import (
    make_security_boundary_node,
    make_understand_request_node,
)
from app.agent.nodes.validate_tool import validate_tool
from app.agent.nodes.workflow_lifecycle import (
    make_handle_workflow_interruption_node,
    make_restore_suspended_workflow_node,
)
from app.agent.schemas import AgentErrorCategory, AgentRequestType, Intent
from app.agent.state import AgentState
from app.memory.service import MemoryService
from app.observability.metrics import get_metrics
from app.observability.tracing import span
from app.policies.confirmation import Clock
from app.policies.engine import PolicyEngine
from app.policies.models import PolicyOutcome
from app.policies.repository import PolicyAuditRepository
from app.rag.answer_generator import GroundedAnswerGenerator
from app.rag.interfaces import KnowledgeRetriever
from app.resilience.config import ResilienceConfig
from app.resilience.control import ReliabilityController
from app.tools import registry
from app.ui.projection import record_node


def _after_context(state: AgentState) -> str:
    return "respond" if state.get("error_category") is not None else "security_boundary"


def _after_security_boundary(state: AgentState) -> str:
    return "respond" if state.get("security_signal") is not None else "retrieve_memory"


def _after_pending(state: AgentState) -> str:
    confirmation_status = state.get("confirmation_status") or "normal"
    if confirmation_status == "resume_suspended":
        return "restore_suspended_workflow"
    if confirmation_status == "inspect_interruption":
        return "understand_request"
    if confirmation_status == "normal" and state.get("workflow_active"):
        return "resume_workflow"
    return {
        "normal": "understand_request",
        "confirmed": "restore_pending_action",
        "no_pending": "respond",
        "resume_unavailable": "respond",
        "rejected": "respond",
        "expired": "respond",
        "ambiguous": "respond",
        "ownership_error": "respond",
    }.get(confirmation_status, "respond")


def _after_workflow_resume(state: AgentState) -> str:
    status = state.get("workflow_resume_status")
    if status == "resumed":
        return "compile_decision"
    if status == "inspect_interruption":
        return "understand_request"
    if status == "waiting_for_fields":
        return "respond"
    return "understand_request"


def _after_understanding(state: AgentState) -> str:
    if state.get("error_category") is not None:
        return "respond"
    if state.get("workflow_interruption_pending"):
        return "handle_workflow_interruption"
    return "compile_decision"


def _after_workflow_interruption(state: AgentState) -> str:
    return (
        "compile_decision"
        if state.get("workflow_interruption_status") in {"suspended", "superseded"}
        else "respond"
    )


def _after_pending_restore(state: AgentState) -> str:
    return "compile_decision" if state.get("compilation_resumed") else "respond"


def _after_selection(state: AgentState) -> str:
    if state.get("error_category") is not None:
        return "respond"
    compile_result = state.get("compile_result")
    if compile_result is not None and compile_result.status == CompileStatus.CLARIFICATION_REQUIRED:
        return "respond"
    if state.get("intent") in {Intent.MEMORY_REMEMBER, Intent.MEMORY_FORGET}:
        return "memory_action"
    if state.get("request_type") in {
        AgentRequestType.INFORMATIONAL,
        AgentRequestType.UNCLEAR,
        AgentRequestType.KNOWLEDGE_ONLY,
    }:
        if state.get("requires_retrieval"):
            return "retrieve_knowledge"
        return "respond"
    return "validate_tool"


def _after_validation(state: AgentState) -> str:
    if state.get("error_category") is not None:
        return "respond"
    if state.get("confirmation_status") == "confirmed":
        return "policy_revalidation"
    return "evaluate_policy"


def _after_policy(state: AgentState) -> str:
    if state.get("error_category") is not None:
        return "respond"
    decision = state.get("policy_decision")
    if decision is None or decision.outcome == PolicyOutcome.DENY:
        return "respond"
    if decision.outcome == PolicyOutcome.ALLOW:
        return "execute_tool"
    if decision.outcome == PolicyOutcome.REQUIRE_CONFIRMATION:
        return "create_pending_action"
    return "execute_human_escalation"


def _after_revalidation(state: AgentState) -> str:
    return "respond" if state.get("error_category") is not None else "execute_confirmed_action"


def _after_execution(state: AgentState) -> str:
    if state.get("error_category") is not None:
        return "respond"
    return "retrieve_knowledge" if state.get("requires_retrieval") else "respond"


def _load_context(state: AgentState) -> AgentState:
    context = state.get("execution_context")
    if context is None:
        return {
            "intent": Intent.UNKNOWN,
            "request_type": AgentRequestType.UNCLEAR,
            "error_category": AgentErrorCategory.POLICY_DENIED,
            "last_error": "Authenticated execution context is required.",
            "selected_tool": None,
            "tool_result": None,
        }
    if state.get("conversation_id") != context.conversation_id:
        return {
            "intent": Intent.UNKNOWN,
            "request_type": AgentRequestType.UNCLEAR,
            "error_category": AgentErrorCategory.OWNERSHIP_VIOLATION,
            "last_error": "Execution context does not match request state.",
            "selected_tool": None,
            "tool_result": None,
        }
    existing_customer = state.get("conversation_customer_id")
    existing_tenant = state.get("conversation_tenant_id")
    existing_actor_id = state.get("conversation_actor_id")
    existing_actor_type = state.get("conversation_actor_type")
    principal = context.principal
    if (
        (existing_customer is not None and existing_customer != context.effective_customer_id)
        or (existing_tenant is not None and existing_tenant != context.tenant_id)
        or (existing_actor_id is not None and existing_actor_id != principal.actor_id)
        or (existing_actor_type is not None and existing_actor_type != principal.actor_type.value)
    ):
        return {
            "intent": Intent.UNKNOWN,
            "request_type": AgentRequestType.UNCLEAR,
            "error_category": AgentErrorCategory.OWNERSHIP_VIOLATION,
            "last_error": "Conversation belongs to a different execution context.",
            "selected_tool": None,
            "pending_action": state.get("pending_action"),
            "tool_result": None,
        }
    return {
        "conversation_customer_id": context.effective_customer_id,
        "conversation_tenant_id": context.tenant_id,
        "conversation_actor_id": principal.actor_id,
        "conversation_actor_type": principal.actor_type.value,
        "agent_run_id": state["agent_run_id"],
        "retry_count": 0,
        "last_error": None,
        "error_category": None,
        "selected_tool": None,
        "tool_arguments": {},
        "semantic_decision": None,
        "compile_result": None,
        "grounding_status": "not_applicable",
        "grounding_reference_type": None,
        "grounding_trusted_source": None,
        "target_validation_status": "not_recorded",
        "tool_result": None,
        "retrieved_chunks": [],
        "retrieval_metadata": {},
        "answer_grounding": {},
        "knowledge_answer": None,
        "citations": [],
        "requires_retrieval": False,
        "knowledge_query": None,
        "policy_decision": None,
        "confirmation_status": None,
        "tool_execution_status": None,
        "memory_context": [],
        "memory_candidate": None,
        "memory_key": None,
        "memory_operation_status": None,
        "memory_written": None,
        "memory_policy_outcome": None,
        "memory_security_signal": None,
        "memory_summary_requested": False,
        "failure_category": None,
        "degraded_components": [],
        "recovery_action": None,
        "write_outcome_unknown": False,
        "replay_detected": False,
        "idempotency_outcome": None,
        "pending_action_restored": False,
        "restored_fields_count": 0,
        "compilation_resumed": False,
        "workflow_interruption_pending": False,
        "workflow_interruption_status": None,
        "interruption_intent": None,
        "workflow_resume_source": None,
        "workflow_transition": None,
        "workflow_interruption_type": None,
    }


def _inspect_risk(state: AgentState) -> AgentState:
    return {}


def _instrument_node(
    name: str, node: Callable[[AgentState], AgentState]
) -> Callable[[AgentState], AgentState]:
    def instrumented(state: AgentState) -> AgentState:
        import time

        started = time.perf_counter()
        with span(
            f"agent.{name}", attributes={"agent.node": name, "node.name": name}
        ) as active_span:
            try:
                if name in {"execute_tool", "escalate"}:
                    result = _instrument_tool_node(name, node, state)
                else:
                    result = node(state)
                error = result.get("error_category")
                active_span.set_attribute("node.status", "error" if error else "ok")
                if error is not None:
                    active_span.set_attribute("error.category", error.value)
                selected_tool = result.get("selected_tool")
                if isinstance(selected_tool, str):
                    active_span.set_attribute("tool.selected", selected_tool)
                recovery = result.get("recovery_action")
                if isinstance(recovery, str):
                    active_span.set_attribute("recovery.action", recovery)
                    if recovery in {"degraded", "continue_without_memory"}:
                        get_metrics().degraded_requests_total.add(1, {"component": name})
                trace_metadata: dict[str, str | int | float | bool] = {}
                if name == "check_pending_action":
                    previous_action = state.get("pending_action")
                    confirmation_status = result.get("confirmation_status")
                    trace_metadata = {
                        "previous_pending_action": (
                            previous_action.tool_name if previous_action is not None else "none"
                        ),
                        "confirmation_detected": confirmation_status in {"confirmed", "rejected"},
                        "confirmation_result": (
                            "approved"
                            if confirmation_status == "confirmed"
                            else str(confirmation_status or "not_recorded")
                        ),
                    }
                elif name == "execute_tool":
                    execution_status = result.get("tool_execution_status") or state.get(
                        "tool_execution_status"
                    )
                    trace_metadata = {
                        "tool_execution": str(execution_status or "not_recorded"),
                    }
                elif name in {"security_boundary", "understand_request"} and result.get(
                    "security_signal"
                ):
                    security_signal = str(result["security_signal"])
                    trace_metadata = {
                        "security_signal": security_signal,
                        "decision": "deny",
                        "reason": security_signal,
                        "execution": "not_attempted",
                        "authority": "not_granted",
                    }
                elif name == "restore_pending_action":
                    trace_metadata = {
                        "pending_action_restored": bool(
                            result.get("pending_action_restored", False)
                        ),
                        "restored_fields_count": int(result.get("restored_fields_count", 0)),
                        "compilation_resumed": bool(result.get("compilation_resumed", False)),
                    }
                elif name == "memory_action" and result.get("security_signal"):
                    trace_metadata = {
                        "security_signal": str(result["security_signal"]),
                        "decision": "deny",
                        "reason": str(
                            result.get("decision_reason")
                            or result.get("memory_policy_outcome")
                            or "memory_write_rejected"
                        ),
                        "execution": "not_attempted",
                        "authority": "not_granted",
                    }
                elif name == "handle_workflow_interruption":
                    previous_intent = result.get("previous_workflow_intent") or state.get(
                        "previous_workflow_intent"
                    )
                    interruption_intent = result.get("interruption_intent") or state.get(
                        "interruption_intent"
                    )
                    trace_metadata = {
                        "workflow_state": str(
                            result.get("workflow_interruption_status") or "not_changed"
                        ),
                        "workflow_transition": str(
                            result.get("workflow_transition") or "not_recorded"
                        ),
                        "previous_workflow": str(
                            result.get("previous_workflow_id") or "not_recorded"
                        ),
                        "new_workflow": str(result.get("workflow_id") or "not_recorded"),
                        "previous_workflow_intent": str(
                            getattr(previous_intent, "value", previous_intent or "not_recorded")
                        ),
                        "interruption_intent": str(
                            getattr(
                                interruption_intent,
                                "value",
                                interruption_intent or "not_recorded",
                            )
                        ),
                        "interruption_type": str(
                            result.get("workflow_interruption_type") or "not_recorded"
                        ),
                        "superseded_by": str(result.get("superseded_by") or "not_applicable"),
                    }
                elif name == "restore_suspended_workflow":
                    snapshot = state.get("suspended_workflow")
                    previous_intent = snapshot.get("intent") if snapshot is not None else None
                    trace_metadata = {
                        "workflow_state": "resumed",
                        "workflow_transition": str(
                            result.get("workflow_transition") or "suspended_to_resumed"
                        ),
                        "previous_workflow": str(
                            result.get("previous_workflow_id") or "not_recorded"
                        ),
                        "new_workflow": str(result.get("workflow_id") or "not_recorded"),
                        "previous_workflow_intent": str(
                            getattr(previous_intent, "value", previous_intent or "not_recorded")
                        ),
                        "resume_source": str(
                            result.get("workflow_resume_source") or "not_recorded"
                        ),
                        "interruption_type": str(
                            result.get("workflow_interruption_type") or "explicit_resume"
                        ),
                        "superseded_by": "not_applicable",
                        "restored_fields_count": int(result.get("restored_fields_count", 0)),
                    }
                elif name == "compile_decision" and state.get("compilation_resumed"):
                    trace_metadata = {"compilation_resumed": True}
                elif name == "policy_revalidate":
                    for key in (
                        "original_policy_inputs_hash",
                        "restored_policy_inputs_hash",
                        "policy_input_diff",
                        "original_pending_policy_inputs",
                        "restored_policy_inputs",
                        "original_policy_inputs_normalized",
                        "restored_policy_inputs_normalized",
                        "policy_revalidation_stage",
                        "policy_revalidation_result",
                    ):
                        value = result.get(key)
                        if isinstance(value, (str, int, float, bool)):
                            trace_metadata[key] = value
                record_node(name, "error" if error else "ok", started, trace_metadata)
                return result
            except Exception:
                record_node(name, "error", started)
                raise

    return instrumented


def _instrument_tool_node(
    node_name: str, node: Callable[[AgentState], AgentState], state: AgentState
) -> AgentState:
    import time

    tool_name = state.get("selected_tool") or "unknown"
    metadata = registry.TOOL_REGISTRY.get(tool_name)
    attributes: dict[str, str | int] = {"tool.name": tool_name}
    if metadata is not None:
        attributes.update(
            {
                "tool.operation_type": metadata.operation_type.value,
                "tool.risk_level": metadata.risk_level.value,
            }
        )
    started = time.perf_counter()
    status = "failed"
    with span("tool.execute", attributes=attributes) as tool_span:
        result = node(state)
        result_status = result.get("tool_execution_status")
        status = result_status if isinstance(result_status, str) else "not_executed"
        tool_span.set_attribute("tool.status", status)
        error_category = result.get("error_category")
        if error_category is not None:
            tool_span.set_attribute("error.category", error_category.value)
        metric_labels = {"tool_name": tool_name, "status": status}
        get_metrics().tool_calls_total.add(1, metric_labels)
        if status == "failed":
            get_metrics().tool_errors_total.add(
                1,
                {
                    "tool_name": tool_name,
                    "error_category": str(
                        getattr(error_category, "value", error_category or "unknown")
                    ),
                },
            )
        get_metrics().tool_call_duration_seconds.record(
            time.perf_counter() - started, {"tool_name": tool_name, "status": status}
        )
        if node_name == "escalate" and status == "executed":
            priority = state.get("tool_arguments", {}).get("priority", "unknown")
            tool_span.set_attribute("escalation.priority", str(priority))
            tool_span.set_attribute("escalation.reason_code", "customer_service_request")
            get_metrics().escalations_total.add(1, {"priority": str(priority)})
            tool_span.add_event("escalation.created")
        return result


def build_graph(
    provider: DecisionProposalProvider,
    session: Session,
    checkpointer: BaseCheckpointSaver[str],
    *,
    policy_engine: PolicyEngine,
    clock: Clock,
    ttl_seconds: int,
    audit_repository: PolicyAuditRepository,
    knowledge_retriever: KnowledgeRetriever,
    grounded_generator: GroundedAnswerGenerator,
    memory_service: MemoryService,
    resilience_config: ResilienceConfig,
    reliability_controller: ReliabilityController,
    decision_contract_version: str = "semantic_decision_v3",
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("load_context", cast(Any, _instrument_node("load_context", _load_context)))
    graph.add_node(
        "security_boundary",
        cast(Any, _instrument_node("security_boundary", make_security_boundary_node())),
    )
    graph.add_node(
        "check_pending_action",
        cast(
            Any,
            _instrument_node(
                "check_pending_action",
                make_check_pending_node(clock, ttl_seconds, audit_repository),
            ),
        ),
    )
    graph.add_node(
        "resume_workflow",
        cast(Any, _instrument_node("resume_workflow", make_resume_workflow_node())),
    )
    graph.add_node(
        "restore_pending_action",
        cast(Any, _instrument_node("restore_pending_action", make_restore_pending_node())),
    )
    graph.add_node(
        "handle_workflow_interruption",
        cast(
            Any,
            _instrument_node(
                "handle_workflow_interruption",
                make_handle_workflow_interruption_node(),
            ),
        ),
    )
    graph.add_node(
        "restore_suspended_workflow",
        cast(
            Any,
            _instrument_node(
                "restore_suspended_workflow",
                make_restore_suspended_workflow_node(),
            ),
        ),
    )
    graph.add_node(
        "retrieve_memory",
        cast(
            Any,
            _instrument_node(
                "retrieve_memory",
                make_retrieve_memory_node(
                    memory_service, session, resilience_config, reliability_controller
                ),
            ),
        ),
    )
    graph.add_node(
        "understand_request",
        cast(
            Any,
            _instrument_node(
                "understand_request",
                make_understand_request_node(
                    provider,
                    resilience_config,
                    decision_contract_version,
                    reliability_controller,
                ),
            ),
        ),
    )
    graph.add_node(
        "select_tool_or_response", cast(Any, _instrument_node("route_request", select_tool))
    )
    graph.add_node(
        "compile_decision",
        cast(
            Any,
            _instrument_node(
                "compile_decision",
                make_compile_decision_node(session, decision_contract_version),
            ),
        ),
    )
    graph.add_node(
        "memory_action",
        cast(
            Any,
            _instrument_node("memory_action", make_memory_action_node(memory_service, session)),
        ),
    )
    graph.add_node("validate_tool", cast(Any, _instrument_node("validate_tool", validate_tool)))
    graph.add_node(
        "evaluate_policy",
        cast(
            Any,
            _instrument_node(
                "evaluate_policy", make_evaluate_policy_node(policy_engine, audit_repository, clock)
            ),
        ),
    )
    graph.add_node("inspect_risk", cast(Any, _instrument_node("inspect_risk", _inspect_risk)))
    graph.add_node(
        "create_pending_action",
        cast(
            Any,
            _instrument_node("create_pending_action", make_create_pending_node(clock)),
        ),
    )
    graph.add_node(
        "policy_revalidation",
        cast(
            Any,
            _instrument_node(
                "policy_revalidate",
                make_revalidate_node(session, audit_repository, clock, policy_engine),
            ),
        ),
    )
    graph.add_node(
        "execute_tool",
        cast(
            Any,
            _instrument_node(
                "execute_tool",
                make_confirmed_execution_node(
                    session,
                    resilience_config,
                    audit_repository,
                    clock,
                    reliability_controller,
                ),
            ),
        ),
    )
    graph.add_node(
        "execute_confirmed_action",
        cast(
            Any,
            _instrument_node(
                "execute_tool",
                make_confirmed_execution_node(
                    session,
                    resilience_config,
                    audit_repository,
                    clock,
                    reliability_controller,
                ),
            ),
        ),
    )
    graph.add_node(
        "execute_human_escalation",
        cast(
            Any,
            _instrument_node(
                "escalate",
                make_human_escalation_node(
                    session,
                    audit_repository,
                    clock,
                    resilience_config,
                    reliability_controller,
                ),
            ),
        ),
    )
    graph.add_node(
        "retrieve_knowledge",
        cast(
            Any,
            _instrument_node(
                "retrieve_knowledge",
                make_retrieve_node(
                    knowledge_retriever,
                    grounded_generator,
                    resilience_config,
                    reliability_controller,
                ),
            ),
        ),
    )
    graph.add_node("respond", cast(Any, _instrument_node("respond", respond)))
    graph.add_edge(START, "load_context")
    graph.add_conditional_edges(
        "load_context",
        _after_context,
        {"security_boundary": "security_boundary", "respond": "respond"},
    )
    graph.add_conditional_edges(
        "security_boundary",
        _after_security_boundary,
        {"retrieve_memory": "retrieve_memory", "respond": "respond"},
    )
    graph.add_edge("retrieve_memory", "check_pending_action")
    graph.add_conditional_edges(
        "check_pending_action",
        _after_pending,
        {
            "understand_request": "understand_request",
            "restore_pending_action": "restore_pending_action",
            "restore_suspended_workflow": "restore_suspended_workflow",
            "resume_workflow": "resume_workflow",
            "respond": "respond",
        },
    )
    graph.add_conditional_edges(
        "restore_pending_action",
        _after_pending_restore,
        {"compile_decision": "compile_decision", "respond": "respond"},
    )
    graph.add_conditional_edges(
        "resume_workflow",
        _after_workflow_resume,
        {
            "compile_decision": "compile_decision",
            "understand_request": "understand_request",
            "respond": "respond",
        },
    )
    graph.add_edge("restore_suspended_workflow", "respond")
    graph.add_conditional_edges(
        "understand_request",
        _after_understanding,
        {
            "handle_workflow_interruption": "handle_workflow_interruption",
            "compile_decision": "compile_decision",
            "respond": "respond",
        },
    )
    graph.add_conditional_edges(
        "handle_workflow_interruption",
        _after_workflow_interruption,
        {"compile_decision": "compile_decision", "respond": "respond"},
    )
    graph.add_edge("compile_decision", "select_tool_or_response")
    graph.add_conditional_edges(
        "select_tool_or_response",
        _after_selection,
        {
            "validate_tool": "validate_tool",
            "memory_action": "memory_action",
            "retrieve_knowledge": "retrieve_knowledge",
            "respond": "respond",
        },
    )
    graph.add_conditional_edges(
        "validate_tool",
        _after_validation,
        {
            "evaluate_policy": "evaluate_policy",
            "policy_revalidation": "policy_revalidation",
            "respond": "respond",
        },
    )
    graph.add_edge("evaluate_policy", "inspect_risk")
    graph.add_conditional_edges(
        "inspect_risk",
        _after_policy,
        {
            "execute_tool": "execute_tool",
            "create_pending_action": "create_pending_action",
            "execute_human_escalation": "execute_human_escalation",
            "respond": "respond",
        },
    )
    graph.add_conditional_edges(
        "policy_revalidation",
        _after_revalidation,
        {"execute_confirmed_action": "execute_confirmed_action", "respond": "respond"},
    )
    graph.add_conditional_edges(
        "execute_tool",
        _after_execution,
        {"retrieve_knowledge": "retrieve_knowledge", "respond": "respond"},
    )
    graph.add_edge("create_pending_action", "respond")
    graph.add_edge("memory_action", "respond")
    graph.add_edge("execute_confirmed_action", "respond")
    graph.add_edge("execute_human_escalation", "respond")
    graph.add_edge("retrieve_knowledge", "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
