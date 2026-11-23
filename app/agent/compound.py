"""Read a customer message before deciding: its separate requests and the signals around them.

The decision contract describes one request and is pinned by the evaluation approvals, so the
signals a keyword list used to guess are read here by the model instead, in one call:

* ``requests``: "What are my orders and what is your cancellation policy?" holds two. Each part
  must stay in the customer's words: no part may carry a number the message did not, and at most
  three parts are kept; otherwise the message stays whole.
* ``status_question`` per request: "Where is my refund for order 6?" asks about something that
  exists, not for a new refund.
* ``upset``: the customer is plainly frustrated, so a handoff starts with an apology and skips
  the offer step.
* ``reply_language``: the customer asked for replies in a given language from now on.

Without a model (tests, offline) or when the call fails, the message is one request with no
signals, and the callers fall back to their conservative defaults.
"""

import json
import logging
import re
from typing import Literal, Protocol

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.config import Settings

logger = logging.getLogger(__name__)

MAX_PARTS = 3
_MAX_PART_LENGTH = 1000
_NUMBER = re.compile(r"\d+")

_INSTRUCTION = (
    "You read one customer-support message and return JSON only, in this shape: "
    '{"requests": [{"text": "...", "status_question": false}], "upset": false, '
    '"reply_language": null}.\n'
    "requests: the message's separate requests, in the order asked; one item when it is a "
    "single request. A separate request could be handled on its own: looking something up, an "
    "action such as a refund, cancellation or support ticket, or a policy question. Separate "
    "questions are separate requests, even about the same order, and the same question about "
    'several orders or tickets ("Where are orders 1 and 2?") becomes one request per order or '
    "ticket. Details of one request, such as the reason for a refund, stay in that request. "
    "Write each request in the customer's own language and words; repeat an order or ticket "
    "number in a later request only when the customer clearly meant the same one. Never add "
    "facts, numbers or requests.\n"
    "status_question: true when that request asks about the state or progress of something "
    "that already exists (an order, a refund, a cancellation, a ticket) rather than asking to "
    "start something new or asking what a policy says.\n"
    "upset: true only when the customer is plainly frustrated, angry or complaining about the "
    "service, in any language or wording.\n"
    'reply_language: "english" or "turkish" only when the customer explicitly asks to be '
    "answered in that language; otherwise null."
)


class ReadRequest(BaseModel):
    text: str
    status_question: bool = False


class MessageReading(BaseModel):
    requests: list[ReadRequest]
    # None means unknown (no model): callers use their own conservative default.
    upset: bool | None = None
    reply_language: Literal["english", "turkish"] | None = None


class MessageReader(Protocol):
    def read(self, message: str) -> dict[str, object]: ...


class OpenAICompatibleReader:
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

    def read(self, message: str) -> dict[str, object]:
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
        if not isinstance(payload, dict):
            raise ValueError("message reading is not an object")
        return payload


def parts_are_faithful(message: str, parts: list[str]) -> bool:
    """Every part is non-empty, short, and carries only numbers the message carried."""

    allowed = set(_NUMBER.findall(message))
    return all(
        part.strip() and len(part) <= _MAX_PART_LENGTH and set(_NUMBER.findall(part)) <= allowed
        for part in parts
    )


def read_message(reader: MessageReader | None, message: str) -> MessageReading:
    """The message's requests and signals; one request with no signals when unread."""

    whole = MessageReading(requests=[ReadRequest(text=message)])
    if reader is None:
        return whole
    try:
        reading = MessageReading.model_validate(reader.read(message))
    except Exception as error:  # noqa: BLE001 - reading is best effort
        logger.warning(
            "Message reading failed.", extra={"reading_error_type": type(error).__name__}
        )
        return whole
    if not reading.requests:
        return whole
    parts = [request.text.strip() for request in reading.requests]
    if len(parts) < 2 or len(parts) > MAX_PARTS or not parts_are_faithful(message, parts):
        if len(parts) >= 2:
            logger.warning("Request split rejected.", extra={"split_part_count": len(parts)})
        # One request: keep the customer's exact words, and the model's reading of them.
        first = reading.requests[0] if len(reading.requests) == 1 else ReadRequest(text=message)
        requests = [ReadRequest(text=message, status_question=first.status_question)]
    else:
        requests = [
            ReadRequest(text=text, status_question=request.status_question)
            for text, request in zip(parts, reading.requests, strict=True)
        ]
    return reading.model_copy(update={"requests": requests})
