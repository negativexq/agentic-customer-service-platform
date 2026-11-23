"""Split a message that asks for several separate things, so each one gets handled.

"What are my orders and what is your cancellation policy?" holds two requests; the decision
contract describes one. Before deciding, a message that may be compound is split by the model
under a strict "split only" instruction. Each part must stay in the customer's words: no part may
carry a number the message did not, and at most three parts are kept. Anything else, a failure
or a slow answer, keeps the message whole, which is today's behaviour.
"""

import json
import logging
import re
from typing import Protocol

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import Settings

logger = logging.getLogger(__name__)

MAX_PARTS = 3
_MAX_PART_LENGTH = 1000
_NUMBER = re.compile(r"\d+")
# Only messages with a joining word or two questions are sent to the model.
_JOINER = re.compile(
    r"\b(?:and|also|plus|then|as well|another|ayrıca|bir de|ve|hem|sonra|daha sonra|ayrica)\b"
    r"|;|\?.*\?",
    re.I | re.S,
)

_INSTRUCTION = (
    "You split a customer-support message into its separate requests. A separate request is "
    "one that could be handled on its own: looking something up, an action such as a refund, "
    "cancellation or support ticket, or a policy question. Details of one request, such as the "
    "reason for a refund, stay in that request. The same question about several orders or "
    'tickets ("Where are orders 1 and 2?") becomes one request per order or ticket. '
    "Write each request in the customer's own "
    "language and words; repeat an order or ticket number in a later request only when the "
    "customer clearly meant the same one. Never add facts, numbers or requests. Return JSON "
    'only: {"requests": ["...", "..."]}, in the order the customer asked, with one item when '
    "the message is a single request."
)


class RequestSplitter(Protocol):
    def split(self, message: str) -> list[str]: ...


class OpenAICompatibleSplitter:
    def __init__(self, settings: Settings) -> None:
        kwargs: dict[str, object] = {
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "api_key": settings.llm_api_key or "not-needed",
            "timeout": httpx.Timeout(
                connect=settings.llm_connect_timeout_seconds,
                read=settings.llm_timeout_seconds,
                write=settings.llm_timeout_seconds,
                pool=settings.llm_connect_timeout_seconds,
            ),
            "max_retries": 0,
        }
        if settings.llm_reasoning_effort is not None:
            kwargs["reasoning_effort"] = settings.llm_reasoning_effort
        if settings.llm_reasoning_effort in {None, "none"}:
            kwargs["temperature"] = 0
        if settings.llm_api == "responses":
            kwargs["use_responses_api"] = True
        self._model = ChatOpenAI(**kwargs)

    def split(self, message: str) -> list[str]:
        result = self._model.invoke(
            [SystemMessage(content=_INSTRUCTION), HumanMessage(content=message)]
        )
        content = result.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        text = str(content).strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        payload = json.loads(text)
        requests = payload.get("requests") if isinstance(payload, dict) else None
        if not isinstance(requests, list):
            raise ValueError("split result has no request list")
        return [str(item) for item in requests]


def may_be_compound(message: str) -> bool:
    return len(message) >= 15 and bool(_JOINER.search(message))


def parts_are_faithful(message: str, parts: list[str]) -> bool:
    """Every part is non-empty, short, and carries only numbers the message carried."""

    allowed = set(_NUMBER.findall(message))
    return all(
        part.strip() and len(part) <= _MAX_PART_LENGTH and set(_NUMBER.findall(part)) <= allowed
        for part in parts
    )


def split_request(splitter: RequestSplitter | None, message: str) -> list[str]:
    """The message's separate requests, in order; the whole message when it is one request."""

    if splitter is None or not may_be_compound(message):
        return [message]
    try:
        parts = [part.strip() for part in splitter.split(message)]
    except Exception as error:  # noqa: BLE001 - splitting is best effort
        logger.warning("Request split failed.", extra={"split_error_type": type(error).__name__})
        return [message]
    if len(parts) < 2 or len(parts) > MAX_PARTS or not parts_are_faithful(message, parts):
        if len(parts) >= 2:
            logger.warning("Request split rejected.", extra={"split_part_count": len(parts)})
        return [message]
    return parts
