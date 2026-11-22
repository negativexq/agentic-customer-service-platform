from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.schemas import Intent
from app.tools.base import OperationType, RiskLevel
from app.tools.registry import TOOL_REGISTRY


class ActionMode(StrEnum):
    ENABLED = "enabled"
    CONFIRM = "confirm"
    DISABLED = "disabled"


# Escalation is the safety valve for everything else; operators may not switch it off.
LOCKED_ACTIONS = frozenset({"escalate_to_human"})


def allowed_action_modes(tool_name: str) -> list[ActionMode]:
    """Modes an operator may choose. Configuration can only narrow authority, never widen it."""

    metadata = TOOL_REGISTRY[tool_name]
    if tool_name in LOCKED_ACTIONS:
        return [ActionMode.ENABLED]
    if metadata.operation_type == OperationType.WRITE and metadata.risk_level <= RiskLevel.LOW:
        return [ActionMode.ENABLED, ActionMode.CONFIRM, ActionMode.DISABLED]
    return [ActionMode.ENABLED, ActionMode.DISABLED]


class GuidanceRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=600)
    intents: list[Intent] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    source: Literal["manual", "coaching"] = "manual"
    source_run_id: str | None = Field(default=None, max_length=200)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guidance: list[GuidanceRule] = Field(default_factory=list, max_length=50)
    actions: dict[str, ActionMode] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "AgentConfig":
        ids = [rule.id for rule in self.guidance]
        if len(ids) != len(set(ids)):
            raise ValueError("Guidance rule ids must be unique.")
        for tool_name, mode in self.actions.items():
            if tool_name not in TOOL_REGISTRY:
                raise ValueError(f"Unknown action: {tool_name}")
            if mode not in allowed_action_modes(tool_name):
                raise ValueError(f"Mode {mode.value} is not allowed for {tool_name}.")
        return self

    def action_mode(self, tool_name: str) -> ActionMode:
        return self.actions.get(tool_name, ActionMode.ENABLED)

    def active_guidance(self) -> list[GuidanceRule]:
        return [rule for rule in self.guidance if rule.enabled]
