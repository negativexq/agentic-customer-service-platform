import logging
from typing import cast
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from opentelemetry import trace
from sqlalchemy.orm import Session

from app.agent.errors import RuntimeFailureSource, classify_runtime_error
from app.agent.graph import build_graph
from app.agent.llm.base import DecisionProposalProvider
from app.agent.llm.integration import (
    DeterministicIntegrationDecisionProvider,
    DeterministicSemanticDecisionProvider,
    DeterministicSemanticDecisionV3Provider,
)
from app.agent.llm.provider import OpenAICompatibleProvider
from app.agent.schemas import (
    AgentExecutionMode,
    AgentProposal,
    AgentRequestType,
    AgentResponse,
    AgentToolCall,
    Intent,
    ProposalValidationStatus,
    ProviderRunMetadata,
)
from app.agent.state import AgentState
from app.auth.models import ActorType, Principal
from app.core.config import DecisionContractVersion, LLMProvider, Settings, get_settings
from app.core.context import ExecutionContext
from app.memory.service import MemoryService
from app.observability.metrics import get_metrics, record_agent_run_summary
from app.observability.privacy import safe_exception_type
from app.observability.tracing import span
from app.persistence.checkpoint import (
    CheckpointBackend,
    MemoryCheckpointProvider,
    checkpoint_thread_id,
    checkpoint_thread_id_hash,
)
from app.policies.confirmation import Clock, SystemClock
from app.policies.engine import PolicyEngine
from app.policies.repository import (
    PolicyAuditRepository,
    build_policy_audit_repository,
)
from app.rag.answer_generator import GroundedAnswerGenerator
from app.rag.interfaces import (
    KnowledgeRetriever,
    ManagedKnowledgeRetriever,
    ReadyKnowledgeRetriever,
)
from app.rag.retrieval.service import build_default_knowledge_service
from app.resilience.config import ResilienceConfig
from app.resilience.control import ReliabilityController
from app.ui.projection import get_projection_store
from app.ui.repository import AgentRunProjectionRepository, build_agent_run_projection_repository

logger = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(
        self,
        provider: DecisionProposalProvider | None = None,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        checkpoint_backend: CheckpointBackend = CheckpointBackend.MEMORY,
        policy_engine: PolicyEngine | None = None,
        clock: Clock | None = None,
        audit_log: PolicyAuditRepository | None = None,
        confirmation_ttl_seconds: int | None = None,
        knowledge_retriever: KnowledgeRetriever | None = None,
        grounded_generator: GroundedAnswerGenerator | None = None,
        memory_service: MemoryService | None = None,
        resilience_config: ResilienceConfig | None = None,
        projection_repository: AgentRunProjectionRepository | None = None,
        decision_contract_version: DecisionContractVersion | None = None,
        reliability_controller: ReliabilityController | None = None,
    ) -> None:
        settings = get_settings()
        self.settings = settings
        self.decision_contract_version = _resolve_decision_contract(
            settings,
            provider=provider,
            explicit_contract=decision_contract_version,
        )
        self.provider = provider or _build_decision_provider(
            settings, self.decision_contract_version
        )
        self.checkpointer = checkpointer or MemoryCheckpointProvider().checkpointer
        self.checkpoint_backend = checkpoint_backend
        self.policy_engine = policy_engine or PolicyEngine()
        self.clock = clock or SystemClock()
        self.audit_repository_override = audit_log
        # Exposed only when an explicit bounded test/local adapter is injected.
        self.audit_log = audit_log
        self.confirmation_ttl_seconds = (
            confirmation_ttl_seconds or settings.confirmation_ttl_seconds
        )
        self.knowledge_retriever = knowledge_retriever or build_default_knowledge_service(settings)
        self.grounded_generator = grounded_generator or GroundedAnswerGenerator(
            settings.rag_final_context_count
        )
        self.memory_service = memory_service or MemoryService(
            enabled=settings.memory_enabled,
            max_context_items=settings.memory_max_context_items,
            default_ttl_days=settings.memory_default_ttl_days,
            support_context_ttl_days=settings.memory_support_context_ttl_days,
        )
        self.resilience_config = resilience_config or ResilienceConfig.from_settings(settings)
        self.reliability_controller = reliability_controller or ReliabilityController(
            self.resilience_config
        )
        self.projection_repository_override = projection_repository

    def close(self) -> None:
        if isinstance(self.knowledge_retriever, ManagedKnowledgeRetriever):
            self.knowledge_retriever.close()

    def is_ready(self) -> bool:
        if isinstance(self.knowledge_retriever, ReadyKnowledgeRetriever):
            return self.knowledge_retriever.is_ready()
        return True

    def readiness_category(self) -> str:
        """Return the retrieval boundary's safe readiness category after a check."""

        category = getattr(self.knowledge_retriever, "readiness_category", None)
        if callable(category):
            return str(category())
        return str(getattr(self.knowledge_retriever, "last_readiness_category", "ready"))

    def run(
        self,
        *,
        message: str,
        session: Session,
        context: ExecutionContext | None = None,
        conversation_id: str | None = None,
        customer_id: int | None = None,
        execution_mode: AgentExecutionMode = AgentExecutionMode.RECORDED_REPLAY,
    ) -> AgentResponse:
        execution_context = context or _legacy_execution_context(conversation_id, customer_id)
        self.reliability_controller.enforce_request_limits(execution_context)
        conversation_id = execution_context.conversation_id
        customer_id = execution_context.effective_customer_id
        thread_id = checkpoint_thread_id(execution_context)
        thread_id_hash = checkpoint_thread_id_hash(execution_context)
        provider, decision_contract_version, actual_mode, fallback_message = (
            self._provider_for_execution(execution_mode)
        )
        # A checkpoint thread is conversation/workflow identity.  Every call to
        # run(), including confirmation and replay requests, is a new graph
        # invocation and therefore gets its own run identity.
        agent_run_id = str(uuid4())
        metric = get_metrics()
        labels = {"status": "ok"}
        metric.agent_runs_total.add(1)
        with span(
            "agent.run",
            attributes={
                "agent.run_id": agent_run_id,
                "request.id": execution_context.request_id,
                "actor.type": execution_context.principal.actor_type.value,
                "actor.roles": execution_context.principal.roles,
                "checkpoint.backend": self.checkpoint_backend.value,
            },
        ) as root_span:
            import time

            started = time.perf_counter()
            try:
                current_span = trace.get_current_span().get_span_context()
                trace_id = f"{current_span.trace_id:032x}" if current_span.is_valid else None
                projection_store = get_projection_store()
                projection_repository = self.projection_repository_override or (
                    build_agent_run_projection_repository(self.settings, session)
                )
                audit_repository = self.audit_repository_override or build_policy_audit_repository(
                    self.settings, session
                )
                graph = build_graph(
                    provider,
                    session,
                    self.checkpointer,
                    policy_engine=self.policy_engine,
                    clock=self.clock,
                    ttl_seconds=self.confirmation_ttl_seconds,
                    audit_repository=audit_repository,
                    knowledge_retriever=self.knowledge_retriever,
                    grounded_generator=self.grounded_generator,
                    memory_service=self.memory_service,
                    resilience_config=self.resilience_config,
                    reliability_controller=self.reliability_controller,
                    decision_contract_version=decision_contract_version,
                )
                with projection_store.capture(
                    run_id=agent_run_id,
                    context=execution_context,
                    trace_id=trace_id,
                ) as projection:
                    state = cast(
                        AgentState,
                        graph.invoke(
                            {
                                "execution_context": execution_context,
                                "conversation_id": conversation_id,
                                "agent_run_id": agent_run_id,
                                "messages": [{"role": "user", "content": message}],
                            },
                            config={
                                "configurable": {
                                    "thread_id": thread_id,
                                },
                                "metadata": {
                                    "checkpoint_backend": self.checkpoint_backend.value,
                                    "thread_id_hash": thread_id_hash,
                                    "actor_id": execution_context.principal.actor_id,
                                    "actor_type": execution_context.principal.actor_type.value,
                                    "effective_customer_id": customer_id,
                                },
                            },
                        ),
                    )
                    response = _response_from_state(
                        state,
                        execution_mode=actual_mode,
                        fallback_message=fallback_message,
                    )
                    try:
                        view = projection_store.build_view(
                            projection,
                            response=response,
                            state=state,
                            policy_events=audit_repository.list_for_agent_run(
                                agent_run_id, tenant_id=execution_context.tenant_id
                            ),
                            duration_ms=(time.perf_counter() - started) * 1000,
                        )
                        projection.view = view
                        projection_repository.upsert(view)
                    except Exception as error:
                        # The projection is observational. Never turn a committed business
                        # result into a replayable failure because its read model is unavailable.
                        logger.warning(
                            "Agent run projection persistence failed.",
                            extra={
                                "projection_error_type": type(error).__name__,
                                "error_category": classify_runtime_error(
                                    error, source=RuntimeFailureSource.PROJECTION
                                ).category.value,
                                "agent_run_id": agent_run_id,
                            },
                        )
                root_span.set_attribute("agent.intent", response.intent.value)
                root_span.set_attribute("agent.request_type", response.request_type.value)
                if response.error_category is not None:
                    labels["status"] = "error"
                    labels["error_category"] = response.error_category.value
                    root_span.set_attribute("agent.status", "error")
                    root_span.set_attribute("error.category", response.error_category.value)
                else:
                    root_span.set_attribute("agent.status", "ok")
                return response
            except Exception as error:
                classification = classify_runtime_error(error, source=RuntimeFailureSource.RUNTIME)
                labels["status"] = "error"
                root_span.set_attribute("agent.status", "error")
                root_span.set_attribute("error.category", classification.category.value)
                root_span.add_event(
                    "agent.persistence_or_execution_error",
                    attributes={
                        "checkpoint.backend": self.checkpoint_backend.value,
                        "error.type": safe_exception_type(error),
                        "error.category": classification.category.value,
                    },
                )
                raise
            finally:
                duration_seconds = time.perf_counter() - started
                metric.agent_run_duration_seconds.record(duration_seconds, labels)
                record_agent_run_summary(
                    duration_seconds=duration_seconds,
                    error=labels["status"] == "error",
                )
                if labels["status"] == "error":
                    metric.agent_errors_total.add(1, labels)

    def _provider_for_execution(
        self, requested_mode: AgentExecutionMode
    ) -> tuple[
        DecisionProposalProvider,
        DecisionContractVersion,
        AgentExecutionMode,
        str | None,
    ]:
        if requested_mode != AgentExecutionMode.LIVE_PROPOSAL:
            return self.provider, self.decision_contract_version, requested_mode, None
        if (
            self.settings.llm_provider != LLMProvider.OPENAI_COMPATIBLE
            or not self.settings.llm_api_key
            or not _is_openai_api_base_url(self.settings.llm_base_url)
        ):
            return (
                DeterministicSemanticDecisionV3Provider(),
                "semantic_decision_v3",
                AgentExecutionMode.RECORDED_REPLAY,
                "Live model unavailable. Showing bounded evidence replay.",
            )
        live_settings = self.settings.model_copy(
            update={
                "agent_decision_contract_version": "semantic_decision_v3",
                "llm_structured_output_mode": self.settings.llm_structured_output_mode,
            }
        )
        return (
            OpenAICompatibleProvider(live_settings),
            "semantic_decision_v3",
            AgentExecutionMode.LIVE_PROPOSAL,
            None,
        )


def _legacy_execution_context(
    conversation_id: str | None, customer_id: int | None
) -> ExecutionContext:
    if conversation_id is None or customer_id is None:
        raise ValueError("Execution context is required.")
    return ExecutionContext(
        request_id=str(uuid4()),
        conversation_id=conversation_id,
        principal=Principal(
            actor_id="legacy-runtime",
            actor_type=ActorType.SUPPORT_OPERATOR,
            roles=["support_operator"],
        ),
        tenant_id="default",
        effective_customer_id=customer_id,
    )


def _build_decision_provider(
    settings: Settings, contract_version: DecisionContractVersion | None = None
) -> DecisionProposalProvider:
    selected_contract = contract_version or settings.agent_decision_contract_version
    selected_settings = settings.model_copy(
        update={"agent_decision_contract_version": selected_contract}
    )
    if selected_settings.llm_provider == LLMProvider.DETERMINISTIC_INTEGRATION:
        if selected_contract == "semantic_decision_v2":
            return DeterministicSemanticDecisionProvider()
        if selected_contract == "semantic_decision_v3":
            return DeterministicSemanticDecisionV3Provider()
        return DeterministicIntegrationDecisionProvider()
    return OpenAICompatibleProvider(selected_settings)


def _resolve_decision_contract(
    settings: Settings,
    *,
    provider: DecisionProposalProvider | None,
    explicit_contract: DecisionContractVersion | None,
) -> DecisionContractVersion:
    if explicit_contract is not None:
        return explicit_contract
    if provider is None:
        return settings.agent_decision_contract_version

    declared_contract = getattr(provider, "decision_contract_version", None)
    if declared_contract in {
        "direct_tool_v1",
        "semantic_decision_v2",
        "semantic_decision_v3",
    }:
        return cast(DecisionContractVersion, declared_contract)

    # Injecting a provider is an explicit compatibility/test boundary. Providers
    # without a declared semantic contract retain the legacy direct contract;
    # the normal settings-built runtime never enters this branch.
    return "direct_tool_v1"


def _response_from_state(
    state: AgentState,
    *,
    execution_mode: AgentExecutionMode,
    fallback_message: str | None,
) -> AgentResponse:
    provider_metadata = state.get("provider_metadata")
    tool_call = None
    if state.get("selected_tool") and state.get("tool_execution_status") in {
        "executed",
        "failed",
    }:
        tool_call = AgentToolCall(
            name=state["selected_tool"],
            status=state["tool_execution_status"] or "failed",
            result=state.get("tool_result"),
        )
    return AgentResponse(
        conversation_id=state["conversation_id"],
        agent_run_id=state["agent_run_id"],
        message=state["final_response"],
        intent=state.get("intent", Intent.UNKNOWN),
        request_type=state.get("request_type", AgentRequestType.UNCLEAR),
        tool_call=tool_call,
        pending_action=state.get("pending_action"),
        decision_reason=state.get("decision_reason"),
        error_category=state.get("error_category"),
        citations=state.get("citations", []),
        failure_category=state.get("failure_category"),
        degraded_components=state.get("degraded_components", []),
        recovery_action=state.get("recovery_action"),
        security_signal=state.get("security_signal"),
        write_outcome_unknown=state.get("write_outcome_unknown", False),
        execution_mode=execution_mode,
        provider="OpenAI"
        if execution_mode == AgentExecutionMode.LIVE_PROPOSAL
        else "recorded_evidence",
        model=(provider_metadata.model if provider_metadata is not None else None),
        fallback_message=fallback_message,
        proposal=_proposal_from_state(state),
        provider_metadata=_provider_run_metadata(provider_metadata),
    )


def _is_openai_api_base_url(value: str) -> bool:
    return value.rstrip("/").casefold() == "https://api.openai.com/v1"


def _provider_run_metadata(value: object) -> ProviderRunMetadata | None:
    if isinstance(value, ProviderRunMetadata):
        return value
    if not isinstance(value, dict):
        return None
    provider = value.get("provider")
    if not isinstance(provider, str):
        return None
    return ProviderRunMetadata.model_validate(value)


def _proposal_from_state(state: AgentState) -> AgentProposal | None:
    intent = state.get("intent")
    semantic = state.get("semantic_decision")
    if intent is None and semantic is None:
        return None
    extracted: dict[str, str | int | bool] = {}
    target = getattr(semantic, "target", None)
    if target is not None:
        target_type = getattr(target, "type", None)
        if isinstance(target_type, str):
            extracted["target_type"] = target_type
        for field in ("order_id", "ticket_id"):
            value = getattr(target, field, None)
            if isinstance(value, int):
                extracted[field] = value
    compile_result = state.get("compile_result")
    compile_status = getattr(getattr(compile_result, "status", None), "value", None)
    if compile_status in {"compile_rejected", "clarification_required"}:
        validation = ProposalValidationStatus.REJECTED
    elif compile_status is not None:
        validation = ProposalValidationStatus.PASSED
    else:
        validation = ProposalValidationStatus.PENDING
    citations = state.get("citations", [])
    references = [
        str(item.get("citation_id"))
        for item in citations
        if isinstance(item, dict) and item.get("citation_id")
    ][:10]
    action = state.get("selected_tool")
    suggested_action = action if isinstance(action, str) else None
    return AgentProposal(
        intent=(intent.value if isinstance(intent, Intent) else str(intent or "unknown")),
        suggested_action=suggested_action,
        extracted_fields=extracted,
        evidence_references=references,
        validation=validation,
    )
