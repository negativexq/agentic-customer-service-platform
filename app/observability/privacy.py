"""Explicit value catalogs for exported trace diagnostics, never content fields."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from uuid import UUID

from opentelemetry.util.types import AttributeValue

from app.agent.schemas import AgentErrorCategory, AgentRequestType, Intent
from app.auth.models import ActorType
from app.memory.schemas import MemoryType
from app.resilience.errors import FailureCategory

# Keep these fixed catalogs in sync with graph.py and tools/registry.py. Do not
# infer permission from a prefix, string shape, or a client-supplied value.
NODE_NAMES = frozenset(
    {
        "check_pending_action",
        "compile_decision",
        "create_pending_action",
        "escalate",
        "evaluate_policy",
        "execute_tool",
        "handle_workflow_interruption",
        "inspect_risk",
        "load_context",
        "memory_action",
        "policy_revalidate",
        "respond",
        "restore_pending_action",
        "restore_suspended_workflow",
        "resume_workflow",
        "retrieve_knowledge",
        "retrieve_memory",
        "route_request",
        "security_boundary",
        "understand_request",
        "validate_tool",
    }
)
TOOL_NAMES = frozenset(
    {
        "get_customer",
        "get_customer_orders",
        "get_order",
        "get_customer_tickets",
        "get_ticket",
        "create_support_ticket",
        "cancel_order",
        "request_refund",
        "escalate_to_human",
    }
)
PROVIDER_NAMES = frozenset(
    {
        "OpenAICompatibleProvider",
        "DeterministicIntegrationDecisionProvider",
        "DeterministicSemanticDecisionProvider",
        "DeterministicSemanticDecisionV3Provider",
        "FakeDecisionProvider",
        "FakeSemanticDecisionProvider",
        "FakeSemanticDecisionV3Provider",
    }
)
DEPENDENCIES = frozenset({"llm", "retrieval", "tool", "memory", "database", "policy"})
SERVICE_IDENTITIES = (
    DEPENDENCIES
    | frozenset(f"tool:{name}" for name in TOOL_NAMES)
    | frozenset(f"llm:{name}" for name in PROVIDER_NAMES)
    | frozenset(
        {
            "memory:postgres",
            "retrieval:HybridRetriever",
            "retrieval:LocalKnowledgeBackend",
            "retrieval:QdrantKnowledgeBackend",
            "retrieval:KnowledgeService",
        }
    )
)
DOMAIN_SPAN_NAMES = frozenset(f"agent.{name}" for name in NODE_NAMES) | frozenset(
    {
        "agent.run",
        "llm.structured_decision",
        "decision.compile",
        "policy.evaluate",
        "policy.revalidate",
        "confirmation.evaluate",
        "tool.execute",
        "memory.compact",
        "memory.evaluate_candidate",
        "memory.persist",
        "memory.forget",
        "memory.retrieve",
        "rag.retrieve",
        "rag.embed_query",
        "rag.dense_search",
        "rag.sparse_search",
        "rag.fusion",
        "rag.rerank",
        "rag.answer_generate",
        "rag.context_build",
        "resilience.retry",
        "resilience.recovery",
    }
)
EVENT_NAMES = frozenset(
    {
        "escalation.created",
        "agent.persistence_or_execution_error",
        "application.exception",
        "exception",
    }
)
ERROR_TYPES = frozenset(
    {
        "Exception",
        "RuntimeError",
        "ValueError",
        "TypeError",
        "TimeoutError",
        "ConnectionError",
        "KeyError",
        "OSError",
        "CancelledError",
        "ValidationError",
        "OperationalError",
        "DBAPIError",
        "IntegrityError",
        "SQLAlchemyError",
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
        "HTTPException",
        "RequestValidationError",
        "ToolError",
        "ResourceNotFoundError",
        "OwnershipError",
        "InvalidStateTransitionError",
        "DuplicateActionError",
        "ResilienceError",
        "RetryExhaustedError",
        "CircuitOpenError",
        "BulkheadRejectedError",
        "RateLimitExceededError",
        "RetryBudgetExhaustedError",
        "UnknownWriteOutcomeError",
        "AuditPersistenceError",
        "ObservabilityConfigurationError",
    }
)
HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
)
_registered_routes: frozenset[str] = frozenset()


def register_route_templates(routes: Iterable[str]) -> None:
    """Snapshot router-owned templates after include_router(), never request paths."""

    global _registered_routes
    _registered_routes = frozenset(route for route in routes if 0 < len(route) <= 256)


def registered_route_templates() -> frozenset[str]:
    return _registered_routes


def safe_exception_type(value: object) -> str:
    name = value if isinstance(value, str) else type(value).__name__
    if isinstance(name, str) and len(name) <= 128:
        category = name.rsplit(".", 1)[-1]
        if category in ERROR_TYPES:
            return category
    return "unknown"


_CATEGORIES: dict[str, frozenset[str]] = {
    "agent.node": NODE_NAMES,
    "node.name": NODE_NAMES,
    "node.status": frozenset({"ok", "error"}),
    "agent.status": frozenset({"ok", "error"}),
    "agent.intent": frozenset(Intent),
    "semantic.intent": frozenset(Intent),
    "agent.request_type": frozenset(AgentRequestType),
    "actor.type": frozenset(ActorType),
    "checkpoint.backend": frozenset({"memory", "postgres"}),
    "llm.provider": PROVIDER_NAMES,
    "llm.operation": frozenset({"structured_decision"}),
    "llm.status": frozenset({"ok", "error"}),
    "tool.name": TOOL_NAMES | {"unknown"},
    "tool.selected": TOOL_NAMES,
    "compiled.tool": TOOL_NAMES | {"none"},
    "tool.operation_type": frozenset({"read", "write"}),
    "tool.status": frozenset({"executed", "failed", "pending", "not_executed"}),
    "policy.outcome": frozenset(
        {"allow", "deny", "require_confirmation", "require_human", "fail_closed"}
    ),
    "action.status": frozenset(
        {"pending", "confirmed", "rejected", "expired", "executed", "failed", "none"}
    ),
    "confirmation.result": frozenset(
        {
            "normal",
            "confirmed",
            "rejected",
            "expired",
            "ambiguous",
            "no_pending",
            "ownership_error",
            "resume_suspended",
            "resume_unavailable",
            "inspect_interruption",
        }
    ),
    "error.category": frozenset(AgentErrorCategory) | {"unknown"},
    "failure.category": frozenset(FailureCategory),
    "memory.failure_category": frozenset(FailureCategory) | {"internal_error", "tool_failure"},
    "dependency.name": DEPENDENCIES,
    "service.identity": SERVICE_IDENTITIES,
    "recovery.action": frozenset(
        {
            "retry",
            "fail_safely",
            "degraded",
            "continue_without_memory",
            "no_replay",
            "clarify",
            "deny",
        }
    ),
    "memory.operation": frozenset({"remember", "forget"}),
    "memory.type": frozenset(MemoryType),
    "memory.status": frozenset(
        {
            "persisted",
            "deduplicated",
            "disabled",
            "forgotten",
            "not_found",
            "reject",
            "allow",
            "require_explicit",
            "failed",
            "degraded",
            "compacted",
        }
    ),
    "memory.policy_outcome": frozenset(
        {
            "persisted",
            "deduplicated",
            "disabled",
            "forgotten",
            "not_found",
            "reject",
            "allow",
            "require_explicit",
            "failed",
        }
    ),
    "memory.security_signal": frozenset(
        {"memory_security_override_attempt", "memory_authority_claim_rejected"}
    ),
    "rag.backend": frozenset({"local", "qdrant"}),
    "rag.embedding_provider": frozenset({"deterministic", "openai", "huggingface"}),
    "rag.fallback_status": frozenset({"none", "reranker"}),
    "rag.status": frozenset({"ok", "error", "degraded"}),
    "rag.grounding.status": frozenset({"pass", "conflict", "insufficient_evidence", "rejected"}),
    "decision.contract.version": frozenset(
        {"direct_tool_v1", "semantic_decision_v2", "semantic_decision_v3"}
    ),
    "semantic.grounding.status": frozenset(
        {"grounded", "symbolic", "ungrounded", "not_applicable", "invalid"}
    ),
    "semantic.grounding.reference_type": frozenset(
        {"explicit_order", "explicit_ticket", "latest_order", "none"}
    ),
    "semantic.target_admissibility": frozenset(
        {"admissible", "admissible_symbolic_read", "requires_clarification", "invalid"}
    ),
    "compiler.status": frozenset(
        {"compiled_action", "clarification_required", "no_action", "compile_rejected"}
    ),
    "escalation.priority": frozenset({"low", "normal", "medium", "high", "urgent", "unknown"}),
    "escalation.reason_code": frozenset({"customer_service_request"}),
    "http.method": HTTP_METHODS,
    "http.request.method": HTTP_METHODS,
    "http.flavor": frozenset({"1.0", "1.1", "2", "2.0", "3", "3.0"}),
    "network.protocol.version": frozenset({"1.0", "1.1", "2", "2.0", "3", "3.0"}),
    "network.protocol.name": frozenset({"http"}),
    "asgi.event.type": frozenset(
        {"http.request", "http.response.start", "http.response.body", "http.disconnect"}
    ),
}
_SEQUENCES = {
    "actor.roles": frozenset({"customer", "support_operator", "service"}),
    "memory.types": frozenset(MemoryType),
    "policy.reason_codes": frozenset(
        {
            "unknown_tool",
            "known_customer_required",
            "ownership_required",
            "risk_policy_allows_automatic_execution",
            "customer_impacting_write",
            "human_controlled_action",
        }
    ),
}
_BOOLEANS = frozenset(
    {"action.expired", "retry.exhausted", "rag.reranker_enabled", "rag.grounding.accepted"}
)
_INTEGERS = {
    "tool.risk_level": (-1, 3),
    "retry.attempt": (0, 10000),
    "http.status_code": (100, 599),
    "http.response.status_code": (100, 599),
    **dict.fromkeys(
        (
            "memory.result_count",
            "rag.dense_candidates",
            "rag.sparse_candidates",
            "rag.fused_candidates",
            "rag.retrieval_count",
            "rag.final_context_chunks",
            "rag.grounding.unsupported_claim_count",
            "rag.grounding.retrieval_count",
        ),
        (0, 1_000_000),
    ),
}
_RATIOS = frozenset({"rag.grounding.citation_coverage", "rag.grounding.answer_confidence"})


def filter_attributes(
    attributes: Mapping[str, object] | None, *, routes: frozenset[str] = frozenset()
) -> dict[str, AttributeValue]:
    """Deny unknown keys and values; never truncate a sensitive string to fit."""

    safe: dict[str, AttributeValue] = {}
    for key, value in (attributes or {}).items():
        if key in {"agent.run_id", "request.id"}:
            if isinstance(value, str) and len(value) == 36:
                try:
                    if str(UUID(value)) == value:
                        safe[key] = value
                except ValueError:
                    pass
        elif key == "http.route":
            if isinstance(value, str) and value in routes and len(value) <= 256:
                safe[key] = value
        elif key in {"error.type", "exception.type"}:
            safe[key] = safe_exception_type(value)
        elif key in _CATEGORIES:
            if isinstance(value, str) and len(value) <= 128 and value in _CATEGORIES[key]:
                safe[key] = value
        elif key in _SEQUENCES:
            if (
                isinstance(value, (list, tuple))
                and len(value) <= 20
                and all(
                    isinstance(item, str) and len(item) <= 128 and item in _SEQUENCES[key]
                    for item in value
                )
            ):
                safe[key] = tuple(value)
        elif key in _BOOLEANS:
            if isinstance(value, bool):
                safe[key] = value
        elif key in _INTEGERS:
            lower, upper = _INTEGERS[key]
            if type(value) is int and lower <= value <= upper:
                safe[key] = value
        elif key in _RATIOS:
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and 0 <= value <= 1
                and math.isfinite(value)
            ):
                safe[key] = value
    return safe
