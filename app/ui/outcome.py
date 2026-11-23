"""One place that decides what a finished run's status means to an operator.

A run can end with an error category for three very different reasons, and dashboards must
not count them the same way:

* ``error``: the system failed (model, tool, database, internal fault). Someone should look.
* ``blocked``: a safeguard stopped the request on purpose (prompt injection, another
  customer's data, an action the team switched off). The assistant did its job.
* ``declined``: the request could not be carried out as asked and the customer was told why
  (the order already shipped, a refund is already in progress, a detail was missing, a memory
  was not stored). Also the assistant doing its job.
"""

from __future__ import annotations

from app.agent.schemas import AgentErrorCategory

RUN_ERROR = "error"
RUN_BLOCKED = "blocked"
RUN_DECLINED = "declined"

SYSTEM_FAILURES = frozenset(
    {
        AgentErrorCategory.LLM_ERROR,
        AgentErrorCategory.TOOL_ERROR,
        AgentErrorCategory.DEPENDENCY_ERROR,
        AgentErrorCategory.DEPENDENCY_FAILURE,
        AgentErrorCategory.INTERNAL_ERROR,
        AgentErrorCategory.UNKNOWN_WRITE_OUTCOME,
        AgentErrorCategory.RETRIEVAL_ERROR,
        AgentErrorCategory.RERANKER_ERROR,
        AgentErrorCategory.UNKNOWN_TOOL,
    }
)

SAFEGUARDS = frozenset({AgentErrorCategory.OWNERSHIP_VIOLATION, AgentErrorCategory.POLICY_DENIED})

# Decision reasons the projection records when a safeguard, not the business state, stopped a run.
_SAFEGUARD_REASONS = frozenset(
    {
        "cross_customer_access_attempt",
        "instruction_override_attempt",
        "memory_security_override_attempt",
        "Policy verification denied execution authority.",
    }
)
_SYSTEM_PREFIX = "Request blocked: "


def run_status_for_error(
    category: AgentErrorCategory,
    *,
    security_signal: str | None = None,
    failure_category: str | None = None,
    memory_operation: bool = False,
) -> str:
    """Status for a run that ended with ``category``."""

    if failure_category or category in SYSTEM_FAILURES:
        return RUN_ERROR
    if security_signal:
        return RUN_BLOCKED
    # Memory refuses to keep something (not asked explicitly, sensitive content): a decline.
    if memory_operation and category == AgentErrorCategory.POLICY_DENIED:
        return RUN_DECLINED
    if category in SAFEGUARDS:
        return RUN_BLOCKED
    return RUN_DECLINED


def normalize_stored_status(
    status: str,
    *,
    failure_category: str | None,
    decision_reason: str | None,
    path: list[str],
    security_signal: str | None = None,
) -> str:
    """Read older projections, stored before refusals had their own status, the same way.

    Those rows say ``error`` for every refusal. What the run recorded about itself (a failure
    category, the decision reason, the steps it took) is enough to tell the three apart.
    """

    if status != RUN_ERROR or failure_category:
        return status
    reason = decision_reason or ""
    if reason.startswith(_SYSTEM_PREFIX):
        try:
            category = AgentErrorCategory(reason.removeprefix(_SYSTEM_PREFIX).rstrip("."))
        except ValueError:
            return RUN_ERROR
        return run_status_for_error(category)
    if security_signal:
        return RUN_BLOCKED
    if "memory_action" in path and reason not in {
        "cross_customer_access_attempt",
        "instruction_override_attempt",
        "memory_security_override_attempt",
    }:
        return RUN_DECLINED
    if reason in _SAFEGUARD_REASONS:
        return RUN_BLOCKED
    return RUN_DECLINED
