import re
from collections.abc import Callable

from app.agent.nodes.workflow_lifecycle import is_interruption_candidate
from app.agent.schemas import SemanticDecision, SemanticTarget
from app.agent.state import AgentState

_ORDER_FOLLOW_UP = re.compile(
    r"^(?:my\s+)?order(?:\s+(?:number|id))?\s*(?:is\s*)?[:#-]?\s*(\d+)\s*[.!]?$",
    re.IGNORECASE,
)
_NUMERIC_FOLLOW_UP = re.compile(r"^#?\s*(\d+)\s*[.!]?$")


def make_resume_workflow_node() -> Callable[[AgentState], AgentState]:
    def resume_workflow(state: AgentState) -> AgentState:
        decision = state.get("pending_workflow_decision")
        missing = list(state.get("missing_required_fields", []))
        if not state.get("workflow_active") or decision is None or not missing:
            return _reset()

        message = _latest_user_message(state).strip()
        entities = dict(state.get("collected_entities", {}))
        remaining = list(missing)
        updated = decision

        if "order_id" in remaining:
            order_id = _parse_order_id(message)
            if order_id is None:
                return _interruption_or_wait(message)
            updated = updated.model_copy(
                update={"target": SemanticTarget(type="explicit_order", order_id=order_id)}
            )
            entities["order_id"] = order_id
            remaining.remove("order_id")

        if "reason" in remaining:
            # A numeric answer satisfies an order target, not a refund reason.  Keep
            # the workflow alive so the next turn can supply the remaining field.
            if not message or _parse_order_id(message) is not None:
                if is_interruption_candidate(message):
                    return _interruption_or_wait(message)
                return _partial_resume(updated, entities, remaining)
            updated = updated.model_copy(update={"reason": message})
            entities["reason"] = message
            remaining.remove("reason")

        if remaining:
            return _partial_resume(updated, entities, remaining)
        return {
            "semantic_decision": updated,
            "previous_intent": updated.intent,
            "collected_entities": entities,
            "missing_required_fields": [],
            "workflow_active": False,
            "workflow_resume_status": "resumed",
            "workflow_interruption_pending": False,
            "workflow_state": "active",
        }

    return resume_workflow


def _parse_order_id(message: str) -> int | None:
    match = _NUMERIC_FOLLOW_UP.fullmatch(message) or _ORDER_FOLLOW_UP.fullmatch(message)
    if match is None:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def _latest_user_message(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if message["role"] == "user":
            return message["content"]
    return ""


def _reset() -> AgentState:
    return {
        "previous_intent": None,
        "pending_workflow_decision": None,
        "missing_required_fields": [],
        "collected_entities": {},
        "workflow_active": False,
        "workflow_resume_status": "reset",
    }


def _interruption_or_wait(message: str) -> AgentState:
    # Nothing is pending here, only a question the assistant asked. A reply that does not
    # answer it is a new turn ("remember I prefer email", "get me a person"), so let the
    # model read it instead of repeating the question.
    if message.strip() or is_interruption_candidate(message):
        return {
            "workflow_resume_status": "inspect_interruption",
            "workflow_interruption_pending": True,
        }
    return {
        "workflow_resume_status": "waiting_for_fields",
        "workflow_interruption_pending": False,
        "workflow_state": "active",
    }


def _partial_resume(
    decision: SemanticDecision,
    entities: dict[str, str | int | bool],
    remaining: list[str],
) -> AgentState:
    """Persist a merged workflow when one follow-up fills only part of the request."""

    return {
        "semantic_decision": decision,
        "pending_workflow_decision": decision,
        "previous_intent": decision.intent,
        "collected_entities": entities,
        "missing_required_fields": remaining,
        "workflow_active": True,
        "workflow_resume_status": "waiting_for_fields",
        "workflow_interruption_pending": False,
        "workflow_state": "active",
    }
