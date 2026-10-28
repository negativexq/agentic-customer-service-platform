from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePath
from time import perf_counter

from app.agent.compiler_contracts import CompilerReasonCode
from app.agent.schemas import AgentErrorCategory, AgentResponse
from app.agent.state import AgentState
from app.core.config import get_settings
from app.core.context import ExecutionContext
from app.policies.models import PendingActionStatus, PolicyAuditEvent
from app.ui.repository import InMemoryAgentRunProjectionRepository
from app.ui.schemas import (
    AgentRunView,
    UIAnswerGrounding,
    UICompilerDecision,
    UIConfirmationLifecycle,
    UIDecisionEvidence,
    UIGroundingEvidence,
    UIMemoryUsage,
    UIPolicyEvent,
    UIRagDocument,
    UIRetrievalMetadata,
    UITargetValidation,
    UIToolEvent,
    UITraceEvent,
    UIWriteOutcome,
)
from app.ui.trace import trace_event_key_for_node, trace_stage_for_node


@dataclass
class _NodeEvent:
    name: str
    status: str
    duration_ms: float
    timestamp: datetime
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass
class _RunProjection:
    run_id: str
    context: ExecutionContext
    started_at: datetime
    trace_id: str | None
    nodes: list[_NodeEvent] = field(default_factory=list)
    view: AgentRunView | None = None


_current_run: ContextVar[_RunProjection | None] = ContextVar("ui_current_run", default=None)


class UIProjectionStore(InMemoryAgentRunProjectionRepository):
    def __init__(self, max_runs: int = 200) -> None:
        super().__init__(max_projections=max_runs)

    @contextmanager
    def capture(
        self,
        *,
        run_id: str,
        context: ExecutionContext,
        trace_id: str | None,
    ) -> Iterator[_RunProjection]:
        projection = _RunProjection(
            run_id=run_id,
            context=context,
            started_at=datetime.now(UTC),
            trace_id=trace_id,
        )
        token = _current_run.set(projection)
        try:
            yield projection
        finally:
            _current_run.reset(token)

    def record_node(
        self,
        name: str,
        status: str,
        duration_ms: float,
        metadata: dict[str, str | int | float | bool] | None = None,
    ) -> None:
        current = _current_run.get()
        if current is not None:
            current.nodes.append(
                _NodeEvent(
                    name,
                    status,
                    duration_ms,
                    datetime.now(UTC),
                    metadata or {},
                )
            )

    def finish(
        self,
        projection: _RunProjection,
        *,
        response: AgentResponse,
        state: AgentState,
        policy_events: list[PolicyAuditEvent],
        duration_ms: float,
        persist: bool = True,
    ) -> AgentRunView:
        view = self.build_view(
            projection,
            response=response,
            state=state,
            policy_events=policy_events,
            duration_ms=duration_ms,
        )
        if persist:
            self.upsert(view)
        projection.view = view
        return view

    def build_view(
        self,
        projection: _RunProjection,
        *,
        response: AgentResponse,
        state: AgentState,
        policy_events: list[PolicyAuditEvent],
        duration_ms: float,
    ) -> AgentRunView:
        tools: list[UIToolEvent] = []
        selected_tool = state.get("selected_tool")
        execution_status = state.get("tool_execution_status")
        if isinstance(selected_tool, str):
            metadata = state.get("tool_result")
            result_fields = sorted(metadata.keys())[:12] if isinstance(metadata, dict) else []
            tools.append(
                UIToolEvent(
                    name=selected_tool,
                    risk_level=_risk_level(selected_tool),
                    status=_tool_projection_status(execution_status, projection.nodes),
                    duration_ms=_node_duration(projection.nodes, "execute_tool"),
                    result_fields=result_fields,
                )
            )
        policy = [
            UIPolicyEvent(
                event_id=event.event_id,
                request_id=event.request_id,
                conversation_id=event.conversation_id,
                action_id=event.action_id,
                timestamp=event.timestamp,
                stage=event.stage,
                confirmation_status=event.confirmation_status,
                revalidation=event.revalidation,
                execution_status=event.execution_status,
                actor_id=event.actor_id,
                actor_type=event.actor_type.value,
                roles=list(event.roles),
                effective_customer_id=event.effective_customer_id,
                tool_name=event.tool_name,
                risk_level=event.risk_level,
                outcome=event.policy_outcome.value,
                reason_codes=event.reason_codes[:10],
            )
            for event in policy_events
            if event.agent_run_id == projection.run_id
        ]
        chunks = state.get("retrieved_chunks")
        rag_documents = []
        if isinstance(chunks, list):
            for chunk in chunks[:10]:
                if isinstance(chunk, dict):
                    raw_score = chunk.get("rerank_score") or chunk.get("score") or 0.0
                    score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
                    title = _safe_text(chunk.get("title"), "Knowledge document")
                    rag_documents.append(
                        UIRagDocument(
                            citation_id=str(
                                chunk.get("chunk_id") or chunk.get("citation_id") or "unknown"
                            ),
                            title=title,
                            section=_safe_text(chunk.get("section"), "Unknown section"),
                            source=_safe_rag_source(chunk.get("source"), title),
                            score=score,
                        )
                    )
        memory = state.get("memory_context")
        memory_items = memory if isinstance(memory, list) else []
        memory_count = len(memory_items)
        memory_view = UIMemoryUsage(
            item_count=memory_count,
            keys=sorted(
                str(item.get("normalized_key"))
                for item in memory_items
                if isinstance(item, dict) and item.get("normalized_key")
            )[:10],
            types=sorted(
                str(item.get("memory_type"))
                for item in memory_items
                if isinstance(item, dict) and item.get("memory_type")
            )[:10],
            retrieved=memory_count > 0,
            retrieved_count=memory_count,
            items_used=memory_count,
            context_usage="context_enrichment" if memory_count else "not_used",
            purpose="context_enrichment" if memory_count else "not_used",
            decision_influence="context_only" if memory_count else "not_used",
            authority_influence="none" if memory_count else "not_applicable",
        )
        retrieval_metadata = UIRetrievalMetadata.model_validate(
            state.get("retrieval_metadata") or {}
        )
        answer_grounding = UIAnswerGrounding.model_validate(state.get("answer_grounding") or {})
        trace = []
        for node in projection.nodes:
            stage = trace_stage_for_node(node.name)
            trace_metadata: dict[str, str | int | float | bool] = dict(node.metadata)
            event_key = trace_event_key_for_node(node.name)
            if (
                node.name == "handle_workflow_interruption"
                and node.metadata.get("workflow_state") == "superseded"
            ):
                event_key = "workflow.superseded"
            if stage.value == "memory_context":
                trace_metadata = {
                    "items_used": memory_count,
                    "role": "context_enrichment" if memory_count else "not_used",
                }
            if node.name == "retrieve_knowledge":
                grounding_payload = answer_grounding.model_dump(exclude_none=True)
                trace_metadata.update(
                    {
                        key: value
                        for key, value in grounding_payload.items()
                        if isinstance(value, (str, int, float, bool))
                    }
                )
            if node.name == "understand_request":
                provider_metadata = state.get("provider_metadata")
                if provider_metadata is not None:
                    metadata_payload = provider_metadata.model_dump(exclude_none=True)
                    trace_metadata.update(
                        {
                            key: value
                            for key, value in metadata_payload.items()
                            if isinstance(value, (str, int, float, bool))
                        }
                    )
            trace.append(
                UITraceEvent(
                    name=node.name,
                    event_key=event_key,
                    stage=stage,
                    status=node.status,
                    duration_ms=node.duration_ms,
                    timestamp=node.timestamp,
                    metadata=trace_metadata,
                )
            )
        pending_action = state.get("pending_action")
        run_status = "error" if response.error_category else "completed"
        if (
            not response.error_category
            and pending_action is not None
            and pending_action.status == PendingActionStatus.PENDING
        ):
            run_status = "waiting_confirmation"
        decision_reason = _decision_reason(state, policy_events, response)
        decision_evidence = _decision_evidence(
            state,
            policy_events,
            response,
            decision_reason=decision_reason,
        )
        view = AgentRunView(
            run_id=projection.run_id,
            request_id=projection.context.request_id,
            conversation_id=projection.context.conversation_id,
            tenant_id=projection.context.tenant_id,
            action_id=_action_id(state),
            customer_id=projection.context.effective_customer_id,
            actor_id=projection.context.principal.actor_id,
            actor_type=projection.context.principal.actor_type.value,
            roles=list(projection.context.principal.roles),
            intent=response.intent.value,
            request_type=response.request_type.value,
            operation_type=(
                "idempotency_replay"
                if state.get("replay_detected")
                else "memory_summary"
                if state.get("memory_summary_requested")
                else "agent_request"
            ),
            status=run_status,
            started_at=projection.started_at,
            duration_ms=duration_ms,
            trace_id=projection.trace_id,
            path=[node.name for node in projection.nodes],
            failure_category=response.failure_category,
            degraded_components=response.degraded_components,
            recovery_action=response.recovery_action,
            memory=memory_view,
            tools=tools,
            policy=policy,
            rag_documents=rag_documents,
            retrieval_metadata=retrieval_metadata,
            answer_grounding=answer_grounding,
            trace=trace,
            decision_reason=decision_reason,
            security_signal=state.get("security_signal"),
            evidence=decision_evidence,
            execution_mode=response.execution_mode.value,
            provider=response.provider,
            model=response.model,
            fallback_message=response.fallback_message,
            proposal=response.proposal,
            provider_metadata=response.provider_metadata,
        )
        return view

    def get_run(self, run_id: str) -> AgentRunView | None:
        return self.get_by_run_id(run_id)

    def conversation(self, conversation_id: str) -> list[AgentRunView]:
        return self.list_for_conversation(conversation_id)


def record_node(
    name: str,
    status: str,
    started: float,
    metadata: dict[str, str | int | float | bool] | None = None,
) -> None:
    current = _current_run.get()
    if current is not None:
        current.nodes.append(
            _NodeEvent(
                name,
                status,
                (perf_counter() - started) * 1000,
                datetime.now(UTC),
                metadata or {},
            )
        )


def current_projection() -> _RunProjection | None:
    return _current_run.get()


def _node_duration(nodes: list[_NodeEvent], name: str) -> float:
    return sum(node.duration_ms for node in nodes if node.name == name)


def _risk_level(tool_name: str) -> int | None:
    from app.tools.registry import TOOL_REGISTRY

    metadata = TOOL_REGISTRY.get(tool_name)
    return int(metadata.risk_level) if metadata else None


def _decision_evidence(
    state: AgentState,
    policy_events: list[PolicyAuditEvent],
    response: AgentResponse,
    *,
    decision_reason: str | None,
) -> UIDecisionEvidence:
    compile_result = state.get("compile_result")
    compiler_status = getattr(getattr(compile_result, "status", None), "value", None)
    compiler_reason: str | None = None
    if compiler_status in {"clarification_required", "compile_rejected"}:
        compiler_reason = _bounded_text(
            getattr(compile_result, "rejection_reason", None)
            or getattr(compile_result, "reason", None)
        )
    policy_decision = state.get("policy_decision")
    pending_action = state.get("pending_action")
    confirmation_required = bool(
        policy_decision is not None
        and getattr(getattr(policy_decision, "outcome", None), "value", None)
        == "require_confirmation"
    )
    confirmation_status: str
    confirmation_action_id: str | None
    confirmation_risk: int | None
    if pending_action is not None:
        confirmation_status = pending_action.status.value
        confirmation_action_id = pending_action.action_id
        confirmation_risk = pending_action.risk_level
    else:
        confirmation_status = state.get("confirmation_status") or (
            "required" if confirmation_required else "not_required"
        )
        confirmation_action_id = state.get("action_id")
        confirmation_risk = getattr(policy_decision, "risk_level", None)
    selected_tool = state.get("selected_tool")
    write_status = _write_outcome_status(state, response, selected_tool)
    authority = _authority_status(selected_tool, write_status, confirmation_required)
    return UIDecisionEvidence(
        decision=_decision_status(state, policy_events, response),
        reason=decision_reason,
        validation_stage=_validation_stage(state, policy_events, response),
        execution_status=write_status,
        authority=authority,
        grounding=UIGroundingEvidence(
            status=state.get("grounding_status", "not_recorded"),
            reference_type=state.get("grounding_reference_type"),
            trusted_source=state.get("grounding_trusted_source"),
        ),
        compiler=UICompilerDecision(
            reason_code=getattr(compile_result, "reason_code", CompilerReasonCode.NOT_RECORDED),
            missing_fields=getattr(compile_result, "missing_fields", []),
            clarification_fields=getattr(compile_result, "clarification_fields", []),
            status=compiler_status or "not_applicable",
            selected_tool=(
                getattr(compile_result, "selected_tool", None)
                if compile_result is not None
                else None
            ),
            requires_retrieval=bool(
                getattr(compile_result, "requires_retrieval", False)
                if compile_result is not None
                else False
            ),
            reason=compiler_reason,
        ),
        target_validation=UITargetValidation(
            status=state.get("target_validation_status", "not_recorded")
        ),
        confirmation=UIConfirmationLifecycle(
            status=str(confirmation_status),
            required=confirmation_required,
            action_id=confirmation_action_id,
            risk_level=confirmation_risk,
        ),
        write_outcome=UIWriteOutcome(status=write_status),
    )


def _decision_reason(
    state: AgentState,
    policy_events: list[PolicyAuditEvent],
    response: AgentResponse,
) -> str | None:
    """Return a deterministic explanation, never the provider's free-form reason."""

    security_signal = state.get("security_signal")
    if isinstance(security_signal, str) and security_signal:
        return security_signal
    if state.get("replay_detected"):
        return "idempotency_replay_prevented"
    compile_result = state.get("compile_result")
    compiler_status = getattr(getattr(compile_result, "status", None), "value", None)
    if compiler_status in {"clarification_required", "compile_rejected"}:
        return _bounded_text(
            getattr(compile_result, "rejection_reason", None)
            or getattr(compile_result, "reason", None)
        )
    if response.error_category in _BUSINESS_VALIDATION_ERRORS:
        return _bounded_text(
            {
                AgentErrorCategory.RESOURCE_NOT_FOUND: (
                    "Referenced business resource was not found."
                ),
                AgentErrorCategory.DUPLICATE_ACTION: ("Duplicate business action was prevented."),
                AgentErrorCategory.INVALID_STATE: (
                    "Current business state does not permit the requested action."
                ),
            }[response.error_category]
        )
    if response.error_category == AgentErrorCategory.OWNERSHIP_VIOLATION:
        return "cross_customer_access_attempt"
    if response.error_category == AgentErrorCategory.POLICY_DENIED:
        return "Policy verification denied execution authority."
    if policy_events:
        event = policy_events[-1]
        outcome = event.policy_outcome.value
        return _bounded_text(f"Policy outcome: {outcome}.")
    if response.error_category is not None:
        return _bounded_text(f"Request blocked: {response.error_category.value}.")
    return None


_BUSINESS_VALIDATION_ERRORS = {
    AgentErrorCategory.RESOURCE_NOT_FOUND,
    AgentErrorCategory.DUPLICATE_ACTION,
    AgentErrorCategory.INVALID_STATE,
}


def _decision_status(
    state: AgentState,
    policy_events: list[PolicyAuditEvent],
    response: AgentResponse,
) -> str:
    if state.get("security_signal") is not None:
        return "deny"
    if state.get("replay_detected"):
        return "already_completed"
    if response.error_category == AgentErrorCategory.OWNERSHIP_VIOLATION:
        return "deny"
    if response.error_category in _BUSINESS_VALIDATION_ERRORS:
        return "validation_failed"
    if response.error_category == AgentErrorCategory.POLICY_DENIED:
        return "deny"
    compile_result = state.get("compile_result")
    compiler_status = getattr(getattr(compile_result, "status", None), "value", None)
    if compiler_status in {"clarification_required", "compile_rejected"}:
        return str(compiler_status)
    if policy_events:
        return policy_events[-1].policy_outcome.value
    if compiler_status:
        return str(compiler_status)
    return "not_recorded"


def _validation_stage(
    state: AgentState,
    policy_events: list[PolicyAuditEvent],
    response: AgentResponse,
) -> str:
    explicit_stage = state.get("policy_revalidation_stage")
    if isinstance(explicit_stage, str) and explicit_stage:
        return explicit_stage
    if state.get("replay_detected"):
        return "idempotency"
    if response.error_category == AgentErrorCategory.OWNERSHIP_VIOLATION:
        return "authorization"
    if response.error_category in _BUSINESS_VALIDATION_ERRORS:
        return "business_validation"
    if response.error_category == AgentErrorCategory.POLICY_DENIED or policy_events:
        return "policy_evaluation"
    compile_result = state.get("compile_result")
    if compile_result is not None:
        return "decision_compiler"
    if state.get("target_validation_status") not in {None, "not_recorded"}:
        return "target_validation"
    return "not_recorded"


def _tool_projection_status(
    execution_status: object,
    nodes: list[_NodeEvent],
) -> str:
    if execution_status == "executed":
        return "completed"
    if execution_status == "failed" and any(node.name == "execute_tool" for node in nodes):
        return "failed_during_execution"
    return "blocked_before_execution"


def _write_outcome_status(
    state: AgentState,
    response: AgentResponse,
    selected_tool: str | None,
) -> str:
    if state.get("security_signal") is not None:
        return "not_attempted"
    if state.get("replay_detected"):
        return "not_repeated"
    if response.error_category == AgentErrorCategory.OWNERSHIP_VIOLATION:
        return "not_attempted"
    if response.write_outcome_unknown or state.get("write_outcome_unknown"):
        return "unknown"
    if selected_tool is None or not _is_write_tool(selected_tool):
        return "not_applicable"
    execution_status = state.get("tool_execution_status")
    if execution_status == "executed":
        return "executed"
    if execution_status == "failed":
        return "failed"
    pending_action = state.get("pending_action")
    if pending_action is not None and pending_action.status == PendingActionStatus.PENDING:
        return "pending_confirmation"
    if pending_action is not None and pending_action.status in {
        PendingActionStatus.REJECTED,
        PendingActionStatus.EXPIRED,
    }:
        return "blocked"
    return "not_attempted"


def _is_write_tool(tool_name: str) -> bool:
    from app.tools import registry

    metadata = registry.TOOL_REGISTRY.get(tool_name)
    return bool(metadata and metadata.operation_type.value == "write")


def _authority_status(
    selected_tool: str | None,
    execution_status: str,
    confirmation_required: bool,
) -> str:
    if execution_status == "executed":
        return (
            "controlled_execution"
            if selected_tool and _is_write_tool(selected_tool)
            else "read_access"
        )
    if confirmation_required or execution_status == "pending_confirmation":
        return "confirmation_required"
    return "not_granted"


def _bounded_text(value: object, limit: int = 300) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:limit]


def _safe_text(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    return " ".join(value.split())[:200]


def _safe_rag_source(value: object, title: str) -> str:
    """Project a document label, never an absolute/container filesystem path."""

    if not isinstance(value, str) or not value.strip():
        return title
    candidate = value.replace("\\", "/")
    if "/" in candidate:
        return title
    basename = PurePath(candidate).name
    if basename in {"", ".", ".."} or "/" in basename:
        return title
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    if stem.startswith(".venv") or stem in {"site-packages", "app"}:
        return title
    return " ".join(stem.replace("_", " ").replace("-", " ").split())[:200] or title


def _action_id(state: AgentState) -> str | None:
    action_id = state.get("action_id")
    if isinstance(action_id, str) and action_id:
        return action_id
    action = state.get("pending_action")
    return action.action_id if action is not None else None


_projection_store = UIProjectionStore(max_runs=get_settings().agent_run_projection_memory_limit)


def get_projection_store() -> UIProjectionStore:
    return _projection_store
