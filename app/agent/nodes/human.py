from collections.abc import Callable
from typing import cast

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
from app.resilience.errors import UnknownWriteOutcomeError
from app.resilience.retry import run_with_retry
from app.schemas.domain import EscalationResponse
from app.services.conversations import lock_current_agent_turn, queue_for_human
from app.services.idempotency import IdempotencyScope, commit_business_write
from app.tools.base import ToolError


def make_human_escalation_node(
    session: Session,
    audit_repository: PolicyAuditRepository | None = None,
    clock: Clock | None = None,
    resilience_config: ResilienceConfig | None = None,
    reliability_controller: ReliabilityController | None = None,
) -> Callable[[AgentState], AgentState]:
    def execute_human_escalation(state: AgentState) -> AgentState:
        if state.get("selected_tool") != "escalate_to_human":
            return {
                "error_category": AgentErrorCategory.POLICY_DENIED,
                "last_error": "Only the registered escalation tool may use the human path.",
                "tool_execution_status": "failed",
            }
        definition = get_agent_tool_definition("escalate_to_human")
        if definition is None:
            return {
                "error_category": AgentErrorCategory.UNKNOWN_TOOL,
                "last_error": "Escalation tool is not registered.",
                "tool_execution_status": "failed",
            }
        context = state.get("execution_context")
        if context is None:
            return {
                "error_category": AgentErrorCategory.POLICY_DENIED,
                "last_error": "Authenticated execution context is required.",
                "tool_execution_status": "failed",
            }
        execution_started = False
        expected_version = state.get("conversation_ownership_version")
        resulting_version = expected_version
        try:
            arguments = definition.input_model.model_validate(state.get("tool_arguments", {}))
            requested_customer = getattr(arguments, "customer_id", None)
            if requested_customer != context.effective_customer_id:
                return {
                    "error_category": AgentErrorCategory.OWNERSHIP_VIOLATION,
                    "last_error": "Tool customer scope conflicts with execution context.",
                    "tool_execution_status": "failed",
                }
            action_id = state.get("action_id")
            if not action_id:
                return {
                    "error_category": AgentErrorCategory.POLICY_DENIED,
                    "last_error": "Business write is missing its idempotency key.",
                    "tool_execution_status": "failed",
                }
            if audit_repository is not None and clock is not None:
                record_execution_event(state, audit_repository, clock, status="attempted")
                execution_started = True

            def attempt() -> object:
                nonlocal resulting_version
                try:
                    conversation = (
                        lock_current_agent_turn(session, context, expected_version)
                        if expected_version is not None
                        else None
                    )
                    result = definition.execute(
                        session,
                        context,
                        arguments,
                        IdempotencyScope(
                            actor_id=context.principal.actor_id,
                            key=action_id,
                            tenant_id=context.tenant_id,
                        ),
                    )
                    if conversation is not None:
                        queue_for_human(
                            session,
                            conversation,
                            escalation_id=cast(EscalationResponse, result).id,
                            actor_id=context.principal.actor_id,
                        )
                        resulting_version = conversation.ownership_version
                    commit_business_write(session, "escalate_to_human")
                    return result
                except Exception:
                    session.rollback()
                    raise

            result = run_with_retry(
                attempt,
                dependency="tool",
                operation_type="write",
                config=resilience_config,
                controller=reliability_controller,
                service_identity="tool:escalate_to_human",
            )
        except UnknownWriteOutcomeError:
            session.rollback()
            if execution_started and audit_repository is not None and clock is not None:
                record_execution_event(
                    state,
                    audit_repository,
                    clock,
                    status="unknown",
                    failure_category=AgentErrorCategory.UNKNOWN_WRITE_OUTCOME.value,
                )
            return {
                "error_category": AgentErrorCategory.UNKNOWN_WRITE_OUTCOME,
                "last_error": "The escalation outcome could not be confirmed.",
                "failure_category": "tool_timeout",
                "recovery_action": "no_replay",
                "write_outcome_unknown": True,
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
                "error_category": classification.category,
                "last_error": str(error),
                "failure_category": classification.failure_category,
                "tool_execution_status": "failed",
            }
        except Exception as error:
            classification = classify_runtime_error(error, source=RuntimeFailureSource.TOOL)
            if execution_started:
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
                "error_category": classification.category,
                "last_error": "The human escalation path could not be completed.",
                "failure_category": classification.failure_category,
                "tool_execution_status": "failed",
            }
        if audit_repository is not None and clock is not None:
            # Keep post-commit audit failure outside the execution exception handlers so it
            # cannot be mistaken for a safely replayable business failure.
            record_execution_event(state, audit_repository, clock, status="success")
        update: AgentState = {
            "tool_result": serialise_result(result),
            "tool_execution_status": "executed",
            "error_category": None,
            "last_error": None,
        }
        if resulting_version is not None:
            # This run caused the transition, so its own reply is not stale.
            update["conversation_ownership_version"] = resulting_version
        return update

    return execute_human_escalation
