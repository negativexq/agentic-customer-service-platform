from collections.abc import Callable

from sqlalchemy.orm import Session

from app.agent.errors import RuntimeFailureSource, classify_runtime_error
from app.agent.nodes.common import serialise_result
from app.agent.nodes.execution_audit import record_execution_event
from app.agent.schemas import AgentErrorCategory
from app.agent.state import AgentState
from app.agent.tool_catalog import get_agent_tool_definition
from app.policies.confirmation import Clock
from app.policies.repository import PolicyAuditRepository
from app.resilience.config import ResilienceConfig
from app.resilience.control import ReliabilityController
from app.resilience.errors import ResilienceError, RetryExhaustedError, UnknownWriteOutcomeError
from app.resilience.retry import run_with_retry
from app.services.conversations import lock_current_agent_turn
from app.services.idempotency import IdempotencyScope, commit_business_write
from app.tools import registry
from app.tools.base import ToolError


def make_execute_tool_node(
    session: Session,
    resilience_config: ResilienceConfig | None = None,
    audit_repository: PolicyAuditRepository | None = None,
    clock: Clock | None = None,
    reliability_controller: ReliabilityController | None = None,
) -> Callable[[AgentState], AgentState]:
    def execute_tool(state: AgentState) -> AgentState:
        tool_name = state["selected_tool"]
        if tool_name is None:
            return {
                "last_error": "No tool was selected.",
                "error_category": AgentErrorCategory.INVALID_TOOL_ARGUMENTS,
                "tool_execution_status": "failed",
            }
        definition = get_agent_tool_definition(tool_name)
        if definition is None:
            return {
                "last_error": f"Tool {tool_name} is not registered.",
                "error_category": AgentErrorCategory.UNKNOWN_TOOL,
                "tool_execution_status": "failed",
            }
        context = state.get("execution_context")
        if context is None:
            return {
                "last_error": "Authenticated execution context is required.",
                "error_category": AgentErrorCategory.POLICY_DENIED,
                "tool_execution_status": "failed",
            }
        operation_type = registry.get_tool(tool_name).operation_type.value
        execution_started = False
        try:
            arguments = definition.input_model.model_validate(state.get("tool_arguments", {}))
            requested_customer = getattr(arguments, "customer_id", None)
            if requested_customer != context.effective_customer_id:
                return {
                    "last_error": "Tool customer scope conflicts with execution context.",
                    "error_category": AgentErrorCategory.OWNERSHIP_VIOLATION,
                    "tool_execution_status": "failed",
                }
            idempotency = None
            if operation_type == "write":
                action_id = state.get("action_id")
                if not action_id:
                    return {
                        "last_error": "Business write is missing its idempotency key.",
                        "error_category": AgentErrorCategory.POLICY_DENIED,
                        "tool_execution_status": "failed",
                    }
                idempotency = IdempotencyScope(
                    actor_id=context.principal.actor_id,
                    key=action_id,
                    tenant_id=context.tenant_id,
                )
                if audit_repository is not None and clock is not None:
                    record_execution_event(
                        state,
                        audit_repository,
                        clock,
                        status="attempted",
                    )
                    execution_started = True

            def attempt() -> object:
                try:
                    expected_version = state.get("conversation_ownership_version")
                    if operation_type == "write" and expected_version is not None:
                        lock_current_agent_turn(session, context, expected_version)
                    result = definition.execute(session, context, arguments, idempotency)
                    if operation_type == "write":
                        commit_business_write(session, tool_name)
                    return result
                except Exception:
                    session.rollback()
                    raise

            try:
                result = run_with_retry(
                    attempt,
                    dependency="tool",
                    operation_type=operation_type,
                    config=resilience_config,
                    controller=reliability_controller,
                    service_identity=f"tool:{tool_name}",
                )
            except UnknownWriteOutcomeError:
                if execution_started and audit_repository is not None and clock is not None:
                    record_execution_event(
                        state,
                        audit_repository,
                        clock,
                        status="unknown",
                        failure_category=AgentErrorCategory.UNKNOWN_WRITE_OUTCOME.value,
                    )
                return {
                    "last_error": "The write outcome could not be confirmed.",
                    "error_category": AgentErrorCategory.UNKNOWN_WRITE_OUTCOME,
                    "failure_category": "tool_timeout",
                    "recovery_action": "no_replay",
                    "write_outcome_unknown": True,
                    "tool_execution_status": "failed",
                }
            except RetryExhaustedError as error:
                classification = classify_runtime_error(error, source=RuntimeFailureSource.TOOL)
                if execution_started and audit_repository is not None and clock is not None:
                    record_execution_event(
                        state,
                        audit_repository,
                        clock,
                        status="failure",
                        failure_category=classification.category.value,
                    )
                return {
                    "last_error": "The selected dependency could not be reached reliably.",
                    "error_category": classification.category,
                    "failure_category": error.category.value,
                    "recovery_action": "fail_safely",
                    "tool_execution_status": "failed",
                }
            except ResilienceError as error:
                classification = classify_runtime_error(error, source=RuntimeFailureSource.TOOL)
                if execution_started and audit_repository is not None and clock is not None:
                    record_execution_event(
                        state,
                        audit_repository,
                        clock,
                        status="failure",
                        failure_category=classification.category.value,
                    )
                return {
                    "last_error": "The selected dependency could not be reached reliably.",
                    "error_category": classification.category,
                    "failure_category": error.category.value,
                    "recovery_action": "fail_safely",
                    "tool_execution_status": "failed",
                }
            except ToolError as error:
                classification = classify_runtime_error(error, source=RuntimeFailureSource.TOOL)
                session.rollback()
                if execution_started and audit_repository is not None and clock is not None:
                    record_execution_event(
                        state,
                        audit_repository,
                        clock,
                        status="failure",
                        failure_category=classification.category.value,
                    )
                return {
                    "last_error": str(error),
                    "error_category": classification.category,
                    "failure_category": classification.failure_category,
                    "tool_execution_status": "failed",
                }
            except Exception as error:
                classification = classify_runtime_error(error, source=RuntimeFailureSource.TOOL)
                session.rollback()
                if execution_started and audit_repository is not None and clock is not None:
                    record_execution_event(
                        state,
                        audit_repository,
                        clock,
                        status="failure",
                        failure_category=classification.category.value,
                    )
                return {
                    "last_error": "The selected tool could not be executed.",
                    "error_category": classification.category,
                    "failure_category": classification.failure_category,
                    "tool_execution_status": "failed",
                }
            if operation_type == "write" and audit_repository is not None and clock is not None:
                # This is intentionally outside the execution exception handlers. If the
                # committed write's success evidence cannot be stored, surface that operational
                # failure without converting it into a replayable business failure.
                record_execution_event(state, audit_repository, clock, status="success")
            return {
                "tool_result": serialise_result(result),
                "tool_execution_status": "executed",
                "last_error": None,
                "error_category": None,
            }
        except Exception:
            if execution_started:
                session.rollback()
            raise

    return execute_tool
