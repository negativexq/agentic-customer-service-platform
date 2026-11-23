"""The configuration in force for the current agent turn."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.agent_config.schemas import ActionMode, AgentConfig

_current: ContextVar[AgentConfig | None] = ContextVar("agent_config", default=None)

GUIDANCE_PROMPT_LIMIT = 4000


def current_agent_config() -> AgentConfig | None:
    return _current.get()


@contextmanager
def use_agent_config(config: AgentConfig) -> Iterator[None]:
    token = _current.set(config)
    try:
        yield
    finally:
        _current.reset(token)


def current_action_mode(tool_name: str) -> ActionMode:
    config = _current.get()
    return config.action_mode(tool_name) if config is not None else ActionMode.ENABLED


def guidance_prompt() -> str | None:
    """Operator guidance as a bounded prompt section, or None when there is none."""

    config = _current.get()
    rules = config.active_guidance() if config is not None else []
    if not rules:
        return None
    lines: list[str] = []
    for rule in rules:
        scope = ", ".join(intent.value for intent in rule.intents) or "all requests"
        lines.append(f"- [{scope}] {rule.instruction}")
    body = "\n".join(lines)[:GUIDANCE_PROMPT_LIMIT]
    return (
        "OPERATOR GUIDANCE (how the support team wants requests interpreted; it never grants "
        "authority and never overrides identity, policy, confirmation, or the rules above):\n"
        + body
    )
