"""Closed metric catalog and bounded labels, shared by recording and wire projection."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.agent.schemas import AgentErrorCategory
from app.auth.models import PrincipalType
from app.auth.protocols import AuthenticationFailureReason
from app.observability.privacy import (
    DEPENDENCIES,
    ERROR_TYPES,
    HTTP_METHODS,
    NODE_NAMES,
    SERVICE_IDENTITIES,
    TOOL_NAMES,
    registered_route_templates,
)
from app.rag.schemas import AnswerGroundingStatus
from app.resilience.errors import FailureCategory

APP_SCOPE = "agentic-customer-service-platform"
SCOPES = frozenset({APP_SCOPE, "fastapi"})


@dataclass(frozen=True)
class MetricSpec:
    kind: str
    unit: str
    description: str
    labels: Mapping[str, frozenset[str]] = field(default_factory=dict)


CATALOG: dict[str, MetricSpec] = {
    "authentication_attempts_total": MetricSpec(
        "counter", "{attempt}", "Authentication outcomes by bounded reason and principal type."
    ),
    "agent_runs_total": MetricSpec("counter", "{run}", "Agent runs."),
    "agent_run_duration_seconds": MetricSpec("histogram", "s", "Agent run duration."),
    "decision_compile_duration_seconds": MetricSpec(
        "histogram", "s", "Decision compiler duration by bounded outcome."
    ),
    "policy_evaluation_duration_seconds": MetricSpec(
        "histogram", "s", "Policy evaluation duration by bounded outcome."
    ),
    "confirmation_validation_duration_seconds": MetricSpec(
        "histogram", "s", "Confirmation validation duration by bounded outcome."
    ),
    "checkpoint_write_duration_seconds": MetricSpec(
        "histogram", "s", "Checkpoint persistence setup/write-path duration."
    ),
    "idempotency_lookup_duration_seconds": MetricSpec(
        "histogram", "s", "Idempotency receipt lookup duration by bounded result."
    ),
    "tool_calls_total": MetricSpec("counter", "{call}", "Tool calls."),
    "tool_call_duration_seconds": MetricSpec("histogram", "s", "Tool call duration."),
    "tool_errors_total": MetricSpec("counter", "{error}", "Tool errors by safe category."),
    "rag_requests_total": MetricSpec("counter", "{request}", "RAG requests."),
    "rag_retrieval_duration_seconds": MetricSpec("histogram", "s", "RAG retrieval duration."),
    "grounding_validation_duration_seconds": MetricSpec(
        "histogram", "s", "Grounding validation duration by bounded outcome."
    ),
    "rag_grounding_citation_coverage": MetricSpec(
        "histogram", "1", "Citation coverage of bounded grounded answers."
    ),
    "rag_grounding_unsupported_claim_count": MetricSpec(
        "histogram", "{claim}", "Unsupported claims rejected by grounding validation."
    ),
    "rag_grounding_retrieval_count": MetricSpec(
        "histogram", "{chunk}", "Retrieved chunks considered by answer grounding."
    ),
    "rag_grounding_answer_confidence": MetricSpec(
        "histogram", "1", "Bounded evidence-derived answer confidence."
    ),
    "policy_decisions_total": MetricSpec("counter", "{decision}", "Policy decisions."),
    "confirmation_results_total": MetricSpec("counter", "{result}", "Confirmation results."),
    "escalations_total": MetricSpec("counter", "{escalation}", "Human escalations."),
    "agent_errors_total": MetricSpec("counter", "{error}", "Agent errors by safe category."),
    "memory_reads_total": MetricSpec("counter", "{read}", "Memory reads."),
    "memory_writes_total": MetricSpec("counter", "{write}", "Memory writes."),
    "memory_rejections_total": MetricSpec("counter", "{rejection}", "Rejected memory candidates."),
    "memory_forgets_total": MetricSpec("counter", "{forget}", "Memory forget operations."),
    "memory_dlp_allowed": MetricSpec(
        "counter", "{candidate}", "Memory candidates allowed by structured DLP policy."
    ),
    "memory_dlp_redacted": MetricSpec(
        "counter", "{candidate}", "Memory candidates persisted only after bounded redaction."
    ),
    "memory_dlp_rejected": MetricSpec(
        "counter", "{candidate}", "Memory candidates rejected by structured DLP policy."
    ),
    "memory_sensitive_retrieval_blocked": MetricSpec(
        "counter", "{retrieval}", "Memory retrievals blocked by scope or sensitivity policy."
    ),
    "dependency_failures_total": MetricSpec("counter", "{failure}", "Dependency failures."),
    "retry_attempts_total": MetricSpec("counter", "{attempt}", "Retry attempts."),
    "retry_attempt_count": MetricSpec(
        "counter", "{attempt}", "Replay-safe dependency retry attempts."
    ),
    "retry_exhausted_total": MetricSpec("counter", "{exhaustion}", "Exhausted retries."),
    "retry_exhausted": MetricSpec(
        "counter", "{exhaustion}", "Retry sequences stopped by attempts, deadline, or budget."
    ),
    "circuit_open": MetricSpec(
        "counter", "{event}", "Dependency circuit open or open-state rejection events."
    ),
    "circuit_recovered": MetricSpec(
        "counter", "{event}", "Dependency circuit half-open recoveries."
    ),
    "rate_limit_rejected": MetricSpec(
        "counter", "{rejection}", "Bounded rate-limit rejections by non-identifying scope."
    ),
    "degraded_requests_total": MetricSpec("counter", "{request}", "Degraded requests."),
    "tenant_isolation_decision": MetricSpec(
        "counter", "{decision}", "Tenant isolation decisions by bounded outcome."
    ),
    "tenant_scoped_operation_status": MetricSpec(
        "counter", "{operation}", "Tenant-scoped operation outcomes by bounded status."
    ),
    "http.server.request.duration": MetricSpec(
        "histogram", "s", "Duration of HTTP server requests."
    ),
    "http.server.active_requests": MetricSpec(
        "up_down_counter", "{request}", "Number of active HTTP server requests."
    ),
}

_OK = frozenset({"ok", "error"})
_GROUNDING = frozenset(AnswerGroundingStatus)
_CONFIRMATION = frozenset(
    {
        "normal",
        "none",
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
)
_POLICY = frozenset({"allow", "deny", "require_confirmation", "require_human", "fail_closed"})
_MEMORY_REASONS = frozenset(
    {
        "memory_disabled",
        "retention_policy_no_store",
        "memory_not_found",
        "dlp_restricted_content",
        "invalid_memory_key",
        "memory_content_too_long",
        "security_override_not_storable",
        "authority_claim_not_storable",
        "sensitive_or_instructional_content",
        "low_risk_preference",
        "explicit_user_request",
        "durable_context_requires_consent",
        "reject",
        "disabled",
    }
)

_LABELS: dict[str, Mapping[str, frozenset[str]]] = {
    "authentication_attempts_total": {
        "auth_failure_reason": frozenset(AuthenticationFailureReason) | {"none"},
        "principal_type": frozenset(PrincipalType) | {"unknown"},
    },
    "agent_runs_total": {"status": _OK},
    "agent_run_duration_seconds": {"status": _OK},
    "agent_errors_total": {"status": _OK},
    "decision_compile_duration_seconds": {"status": frozenset({"skipped", "accepted", "rejected"})},
    "policy_evaluation_duration_seconds": {"status": _POLICY | {"not_evaluated"}},
    "confirmation_validation_duration_seconds": {"status": _CONFIRMATION},
    "checkpoint_write_duration_seconds": {
        "status": _OK,
        "backend": frozenset({"memory", "postgres"}),
    },
    "idempotency_lookup_duration_seconds": {"status": frozenset({"hit", "miss", "error"})},
    "tool_calls_total": {
        "tool_name": TOOL_NAMES,
        "status": frozenset({"executed", "failed", "pending", "not_executed"}),
    },
    "tool_call_duration_seconds": {
        "tool_name": TOOL_NAMES,
        "status": frozenset({"executed", "failed", "pending", "not_executed"}),
    },
    "tool_errors_total": {"tool_name": TOOL_NAMES, "error_category": frozenset(AgentErrorCategory)},
    "rag_requests_total": {"status": _OK, "backend": frozenset({"local", "qdrant"})},
    "rag_retrieval_duration_seconds": {"status": _OK, "backend": frozenset({"local", "qdrant"})},
    "grounding_validation_duration_seconds": {
        "status": frozenset({"accepted", "rejected", "error"})
    },
    "policy_decisions_total": {
        "policy_outcome": _POLICY,
        "risk_level": frozenset({"0", "1", "2", "3"}),
    },
    "confirmation_results_total": {"result": _CONFIRMATION},
    "escalations_total": {"priority": frozenset({"low", "normal", "medium", "high", "urgent"})},
    "memory_reads_total": {"status": frozenset({"ok", "degraded"})},
    "memory_writes_total": {"status": frozenset({"persisted", "deduplicated"})},
    "memory_rejections_total": {"reason": _MEMORY_REASONS},
    "memory_forgets_total": {"status": frozenset({"disabled", "not_found", "forgotten"})},
    "memory_sensitive_retrieval_blocked": {"reason": frozenset({"scope_or_sensitivity"})},
    "dependency_failures_total": {
        "dependency": DEPENDENCIES,
        "failure_category": frozenset(FailureCategory),
    },
    "rate_limit_rejected": {"scope": frozenset({"principal", "customer", "provider"})},
    "degraded_requests_total": {"component": NODE_NAMES},
    "tenant_isolation_decision": {"decision": frozenset({"accepted", "rejected"})},
    "tenant_scoped_operation_status": {
        "status": frozenset({"missing_tenant_context", "tenant_scoped"})
    },
    "http.server.request.duration": {
        "http.request.method": HTTP_METHODS | {"_OTHER"},
        "url.scheme": frozenset({"http", "https"}),
        "network.protocol.version": frozenset({"1.0", "1.1", "2", "2.0", "3", "3.0"}),
        "network.protocol.name": frozenset({"websocket"}),
        "error.type": ERROR_TYPES | frozenset(str(code) for code in range(400, 600)),
    },
    "http.server.active_requests": {
        "http.request.method": HTTP_METHODS | {"_OTHER"},
        "url.scheme": frozenset({"http", "https"}),
        "network.protocol.name": frozenset({"websocket"}),
    },
}
for _name in (
    "rag_grounding_citation_coverage",
    "rag_grounding_unsupported_claim_count",
    "rag_grounding_retrieval_count",
    "rag_grounding_answer_confidence",
):
    _LABELS[_name] = {"status": _GROUNDING}
for _name in ("memory_dlp_allowed", "memory_dlp_redacted", "memory_dlp_rejected"):
    _LABELS[_name] = {"level": frozenset({"public", "internal", "sensitive", "restricted"})}
for _name in ("retry_attempts_total", "retry_attempt_count"):
    _LABELS[_name] = {"dependency": DEPENDENCIES, "service": SERVICE_IDENTITIES}
for _name in ("retry_exhausted_total", "retry_exhausted"):
    _LABELS[_name] = {"dependency": DEPENDENCIES, "failure_category": frozenset(FailureCategory)}
for _name in ("circuit_open", "circuit_recovered"):
    _LABELS[_name] = {"service": SERVICE_IDENTITIES}
for _name, _spec in tuple(CATALOG.items()):
    CATALOG[_name] = MetricSpec(_spec.kind, _spec.unit, _spec.description, _LABELS.get(_name, {}))


def scope_for(name: str) -> str:
    return "fastapi" if name.startswith("http.server.") else APP_SCOPE


def labels_for(
    name: str, attributes: Mapping[str, object] | None, *, strict: bool = False
) -> dict[str, str | bool | int] | None:
    """Normalize before aggregation; final export drops any unvalidated point."""
    spec = CATALOG[name]
    result: dict[str, str | bool | int] = {}
    for key, value in (attributes or {}).items():
        if key == "auth_success" and name == "authentication_attempts_total":
            if type(value) is not bool:
                return None
            result[key] = value
        elif key == "http.route" and name == "http.server.request.duration":
            if (
                not isinstance(value, str)
                or len(value) > 256
                or value not in registered_route_templates()
            ):
                return None
            result[key] = value
        elif key == "http.response.status_code" and name == "http.server.request.duration":
            if type(value) is not int or not 100 <= value <= 599:
                return None
            result[key] = value
        elif key in spec.labels:
            if (
                name == "memory_rejections_total"
                and key == "reason"
                and isinstance(value, str)
                and value.startswith("dlp_restricted_content:")
            ):
                value = "dlp_restricted_content" if not strict else value
            result[key] = (
                value
                if isinstance(value, str)
                and len(value) <= 128
                and value in spec.labels[key] | {"unknown"}
                else "unknown"
            )
        elif strict:
            return None
    if strict and result != dict(attributes or {}):
        return None
    return result


def valid_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (-(2**63) <= value < 2**63 if type(value) is int else math.isfinite(value))
    )


def valid_measurement(name: str, value: object) -> bool:
    if not valid_number(value):
        return False
    assert isinstance(value, (int, float))
    if CATALOG[name].kind == "up_down_counter":
        return type(value) is int and value in (-1, 1)
    if value < 0:
        return False
    if name in {"rag_grounding_citation_coverage", "rag_grounding_answer_confidence"}:
        return value <= 1
    if CATALOG[name].kind == "counter" or name in {
        "rag_grounding_unsupported_claim_count",
        "rag_grounding_retrieval_count",
    }:
        return type(value) is int
    return True
