"""When a customer asks for a person: offer to help once, or connect them straight away.

Customers should never be kept from a person. The assistant offers help a single time and
only when nothing points to an urgent need: a repeated request, frustrated wording, or a
sensitive matter (the decision marks those high or urgent priority) all hand off at once.
"""

from app.agent.state import AgentState
from app.agent_config.context import current_agent_config

_OFFER_OPENING = "I can connect you with a member of our support team."
HANDOFF_OFFER_MESSAGE = (
    _OFFER_OPENING + " If you tell me what it's about, "
    "I'll try to sort it out right away or make sure it reaches the right person. "
    "If you'd rather talk to someone now, just say so and I'll connect you."
)

URGENT_PRIORITIES = frozenset({"high", "urgent"})

# Sent alongside the pinned system prompt, so the decision marks which requests may not wait.
HANDOFF_PRIORITY_PROMPT = (
    "HAND-OFF PRIORITY: when you return human_escalation, a plain request to speak with a "
    "person is priority medium. Use high or urgent only for sensitive matters (an "
    "unrecognized, unauthorized, or disputed charge; legal, safety, or account-security "
    "concerns) or when the customer is clearly upset. After the assistant offered to help "
    "first: if the customer's latest message explains their issue, handle that issue with its "
    "own intent (for example shipping_policy or order_lookup) instead of human_escalation; "
    "return human_escalation only if they again ask for a person or accept being connected."
)

# Wording that shows the customer is already upset. Asking for a person is not on this
# list: that request alone is what the offer answers.
FRUSTRATION_MARKERS = (
    "useless",
    "not helpful",
    "ridiculous",
    "terrible",
    "frustrat",
    "angry",
    "furious",
    "waste of time",
    "fed up",
    "unacceptable",
    "this is wrong",
    "doesn't work",
    "does not work",
    "again and again",
    "rezalet",
    "saçma",
    "işe yaramıyor",
    "sinir bozucu",
    "kaçıncı",
    "bıktım",
)


def should_offer_before_handoff(state: AgentState) -> bool:
    config = current_agent_config()
    if config is not None and config.handoff == "immediate":
        return False
    messages = state.get("messages", [])
    if any(
        # Later layers may append to the stored reply, so match on its fixed opening.
        item["role"] == "assistant" and item["content"].startswith(_OFFER_OPENING)
        for item in messages
    ):
        return False
    arguments = state.get("tool_arguments") or {}
    if str(arguments.get("priority", "")).casefold() in URGENT_PRIORITIES:
        return False
    latest = next(
        (item["content"] for item in reversed(messages) if item["role"] == "user"), ""
    ).casefold()
    return not any(marker in latest for marker in FRUSTRATION_MARKERS)
