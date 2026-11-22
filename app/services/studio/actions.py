"""Action catalog: registered tools, their safeguards, operator mode and real usage."""

from collections.abc import Sequence

from app.agent.tool_catalog import TOOL_DEFINITIONS
from app.agent_config.schemas import LOCKED_ACTIONS, AgentConfig, allowed_action_modes
from app.services.studio.procedures import PROCEDURES
from app.tools.base import OperationType, RiskLevel
from app.tools.registry import TOOL_REGISTRY
from app.ui.schemas import AgentRunView
from app.ui.studio_schemas import ActionInputField, ActionUsage, ActionView

_RISK = {
    RiskLevel.READ_ONLY: ("Read only", "Runs automatically"),
    RiskLevel.LOW: ("Low", "Runs automatically"),
    RiskLevel.CONFIRMATION_REQUIRED: ("Confirmation", "Customer must confirm; revalidated first"),
    RiskLevel.HUMAN_HANDLING: ("Human handling", "Hands the conversation to a person"),
}

_SUCCESS = {"completed", "succeeded", "success", "executed"}
_FAILED = {"failed", "error", "timeout"}


def _safeguards(name: str) -> list[str]:
    metadata = TOOL_REGISTRY[name]
    items = ["Customer ownership is checked on every call"]
    if metadata.operation_type == OperationType.WRITE:
        items.append("Executed at most once per action (idempotency key)")
    if metadata.risk_level == RiskLevel.CONFIRMATION_REQUIRED:
        items += ["Explicit customer confirmation", "Policy revalidated before execution"]
    if metadata.risk_level == RiskLevel.HUMAN_HANDLING:
        items.append("AI stops replying until an operator responds")
    return items


def _inputs(name: str) -> list[ActionInputField]:
    definition = TOOL_DEFINITIONS.get(name)
    if definition is None:
        return []
    fields: list[ActionInputField] = []
    for field_name, info in definition.input_model.model_fields.items():
        annotation = info.annotation
        type_name = getattr(annotation, "__name__", None) or str(annotation).replace("typing.", "")
        fields.append(
            ActionInputField(
                name=field_name,
                type=type_name,
                required=info.is_required(),
                description=info.description,
            )
        )
    return fields


def _usage(name: str, runs: Sequence[AgentRunView]) -> ActionUsage:
    usage = ActionUsage()
    for run in runs:
        events = [event for event in run.tools if event.name == name]
        policies = [event for event in run.policy if event.tool_name == name]
        if not events and not policies:
            continue
        usage.calls += 1
        statuses = {event.status for event in events}
        if statuses & _SUCCESS:
            usage.succeeded += 1
        elif statuses & _FAILED:
            usage.failed += 1
        outcomes = {event.outcome for event in policies}
        if "deny" in outcomes:
            usage.denied += 1
        elif "require_confirmation" in outcomes and not statuses & _SUCCESS:
            usage.awaiting_confirmation += 1
        if usage.last_used_at is None or run.started_at > usage.last_used_at:
            usage.last_used_at = run.started_at
    return usage


def list_actions(
    published: AgentConfig, draft: AgentConfig | None, runs: Sequence[AgentRunView]
) -> list[ActionView]:
    views: list[ActionView] = []
    for name, metadata in TOOL_REGISTRY.items():
        risk_label, default_policy = _RISK[metadata.risk_level]
        views.append(
            ActionView(
                name=name,
                description=metadata.description,
                operation="write" if metadata.operation_type == OperationType.WRITE else "read",
                risk_level=int(metadata.risk_level),
                risk_label=risk_label,
                default_policy=default_policy,
                allowed_modes=allowed_action_modes(name),
                locked=name in LOCKED_ACTIONS,
                published_mode=published.action_mode(name),
                draft_mode=draft.action_mode(name) if draft is not None else None,
                safeguards=_safeguards(name),
                inputs=_inputs(name),
                procedures=[p.intent for p in PROCEDURES if name in p.actions],
                usage=_usage(name, runs),
            )
        )
    return views
