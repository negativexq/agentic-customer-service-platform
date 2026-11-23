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
* ``explains_issue``: the message says what the customer needs help with, so a request for a
  person in it is handed over at once instead of first offering to help.
* ``reply_language``: the customer asked for replies in a given language from now on.
* ``conversation_move``: a message that asks nothing of the store (hello, chit-chat, goodbye, an
  off-topic question, "who are you?") gets a short human reply instead of a policy search.

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
from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings

logger = logging.getLogger(__name__)

MAX_PARTS = 3
_MAX_PART_LENGTH = 1000
_NUMBER = re.compile(r"\d+")
_MOVES = {"greeting", "small_talk", "closing", "off_topic", "about_assistant", "own_name"}

_INSTRUCTION = (
    "You read one customer-support message and return JSON only, in this shape: "
    '{"requests": [{"text": "...", "status_question": false, "list_position": null}], '
    '"upset": false, "explains_issue": false, '
    '"reply_language": null, "language": "turkish", "withdraws": false, '
    '"asks_memory_summary": false, "acknowledgement": false, "conversation_move": null}.\n'
    "requests: the message's separate requests, in the order asked; one item when it is a "
    "single request. A separate request could be handled on its own: looking something up, an "
    "action such as a refund, cancellation or support ticket, or a policy question. Separate "
    "questions are separate requests, even about the same order, and the same question about "
    'several orders or tickets ("Where are orders 1 and 2?") becomes one request per order or '
    "ticket. Details of one request, such as the reason for a refund, stay in that request. "
    "A request to talk to a person and the problem it is about are one request, never two. "
    "Write each request in the customer's own language and words; repeat an order or ticket "
    "number in a later request only when the customer clearly meant the same one. Never add "
    "facts, numbers or requests.\n"
    "status_question: true when that request asks about the state or progress of something "
    "that already exists (an order, a refund, a cancellation, a ticket) rather than asking to "
    "start something new or asking what a policy says.\n"
    "list_position: when that request points at an item by its place in a list the assistant "
    'just showed ("the second one", "ikincisinin", "the last one"), its 1-based position, '
    "or -1 for the last one; otherwise null.\n"
    "upset: true only when the customer is plainly frustrated, angry or complaining about the "
    "service, in any language or wording, sarcasm included.\n"
    "explains_issue: true when the message says what the customer needs help with (what "
    "happened, or what they want done), beyond only asking to talk to a person; false for a "
    'bare "can I talk to someone?".\n'
    'reply_language: "english" or "turkish" only when the customer explicitly asks to be '
    "answered in that language; otherwise null.\n"
    'language: the language the message is written in: "english", "turkish", "other", or '
    "null when it has no words (a number, an emoji).\n"
    'withdraws: true when the customer drops their own earlier request ("never mind", '
    '"vazgeçtim", "gerek yok") without asking for anything new.\n'
    "asks_memory_summary: true when the customer asks what you remember or know about them.\n"
    'acknowledgement: true when the whole message only acknowledges ("ok", "got it", '
    "a thumbs up) and asks for nothing.\n"
    "conversation_move: set only when the whole message asks nothing about this store's "
    "orders, refunds, cancellations, support tickets, delivery, payments, account or store "
    'policies; otherwise null. "greeting": only says hello. "small_talk": chit-chat such as '
    '"how are you?" or "I\'m tired today". "closing": ends the conversation, a thank-you or '
    'goodbye ("thanks, have a nice day", "bye"). "off_topic": asks for something unrelated to '
    "this store's customer support, such as general knowledge, weather, recipes, jokes, "
    "coding, math, medical, legal, financial or political advice, or product recommendations. "
    '"about_assistant": asks who or what the assistant is (its name, whether it is a person or '
    'a bot). "own_name": asks whether the assistant knows the customer\'s name.'
)


class ReadRequest(BaseModel):
    text: str
    status_question: bool = False
    list_position: int | None = Field(default=None, ge=-1, le=50)


class MessageReading(BaseModel):
    requests: list[ReadRequest]
    # None means unknown (no model): callers use their own conservative default.
    upset: bool | None = None
    explains_issue: bool | None = None
    reply_language: Literal["english", "turkish"] | None = None
    language: Literal["english", "turkish", "other"] | None = None
    withdraws: bool = False
    asks_memory_summary: bool = False
    acknowledgement: bool = False
    conversation_move: (
        Literal["greeting", "small_talk", "closing", "off_topic", "about_assistant", "own_name"]
        | None
    ) = None

    @field_validator("conversation_move", mode="before")
    @classmethod
    def _unknown_move_is_none(cls, value: object) -> object:
        # An unexpected label must not throw away the rest of the reading.
        return value if value in _MOVES else None


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
        # "ok" or "never mind" asks for nothing; its signals still count.
        return reading.model_copy(update={"requests": whole.requests})
    parts = [request.text.strip() for request in reading.requests]
    if len(parts) < 2 or len(parts) > MAX_PARTS or not parts_are_faithful(message, parts):
        if len(parts) >= 2:
            logger.warning("Request split rejected.", extra={"split_part_count": len(parts)})
        # One request: keep the customer's exact words, and the model's reading of them.
        first = reading.requests[0] if len(reading.requests) == 1 else ReadRequest(text=message)
        requests = [
            ReadRequest(
                text=message,
                status_question=first.status_question,
                list_position=first.list_position,
            )
        ]
    else:
        requests = [
            ReadRequest(
                text=text,
                status_question=request.status_question,
                list_position=request.list_position,
            )
            for text, request in zip(parts, reading.requests, strict=True)
        ]
    return reading.model_copy(update={"requests": requests})
