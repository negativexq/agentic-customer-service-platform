from app.agent.schemas import AgentErrorCategory
from app.ui.outcome import (
    RUN_BLOCKED,
    RUN_DECLINED,
    RUN_ERROR,
    normalize_stored_status,
    run_status_for_error,
)


def test_system_failures_are_errors() -> None:
    assert run_status_for_error(AgentErrorCategory.LLM_ERROR) == RUN_ERROR
    assert run_status_for_error(AgentErrorCategory.DEPENDENCY_ERROR) == RUN_ERROR
    assert (
        run_status_for_error(AgentErrorCategory.INVALID_STATE, failure_category="database")
        == RUN_ERROR
    )


def test_safeguards_block_and_business_rules_decline() -> None:
    assert run_status_for_error(AgentErrorCategory.OWNERSHIP_VIOLATION) == RUN_BLOCKED
    assert run_status_for_error(AgentErrorCategory.POLICY_DENIED) == RUN_BLOCKED
    assert (
        run_status_for_error(
            AgentErrorCategory.POLICY_DENIED, security_signal="instruction_override_attempt"
        )
        == RUN_BLOCKED
    )
    assert run_status_for_error(AgentErrorCategory.INVALID_STATE) == RUN_DECLINED
    assert run_status_for_error(AgentErrorCategory.DUPLICATE_ACTION) == RUN_DECLINED
    assert run_status_for_error(AgentErrorCategory.RESOURCE_NOT_FOUND) == RUN_DECLINED
    assert (
        run_status_for_error(AgentErrorCategory.POLICY_DENIED, memory_operation=True)
        == RUN_DECLINED
    )


def test_older_rows_are_read_the_same_way() -> None:
    def read(reason: str | None, path: list[str] | None = None, **extra: str | None) -> str:
        return normalize_stored_status(
            "error",
            failure_category=extra.get("failure_category"),
            decision_reason=reason,
            path=path or ["load_context", "respond"],
            security_signal=extra.get("security_signal"),
        )

    assert read("Duplicate business action was prevented.") == RUN_DECLINED
    assert read("Current business state does not permit the requested action.") == RUN_DECLINED
    assert read("cross_customer_access_attempt") == RUN_BLOCKED
    assert read("instruction_override_attempt", security_signal="instruction_override_attempt") == (
        RUN_BLOCKED
    )
    assert read("Policy verification denied execution authority.") == RUN_BLOCKED
    assert (
        read("Policy verification denied execution authority.", ["memory_action", "respond"])
        == RUN_DECLINED
    )
    assert read("Request blocked: llm_error.") == RUN_ERROR
    assert read(None, failure_category="database_unavailable") == RUN_ERROR
    assert (
        normalize_stored_status("completed", failure_category=None, decision_reason=None, path=[])
        == "completed"
    )
