"""Reply in the customer's language without letting the model change what the reply says.

Replies are composed in English from tool results, policy outcomes, and the knowledge base.
When the customer writes in another supported language, the composed reply is translated by
the model under a strict "translate only" instruction. Every number in the reply (order ids,
amounts, dates) must survive unchanged and no new number may appear; otherwise the English
reply is kept. A failed or slow translation never blocks the answer.
"""

import logging
import re
from typing import Protocol

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Case-sensitive: with IGNORECASE, Python matches the dotless "ı" to an English "I".
_TURKISH_LETTERS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_TURKISH_WORDS = re.compile(
    r"\b(?:merhaba|selam|tesekkur\w*|nasil|lutfen|siparis\w*|iade\w*|iptal\w*|kargo\w*|evet|"
    r"hayir|neden|nerede|tamam|peki|durumda|onun|bunun|var|yok|mi|mu|musun|misin|nedir)\b",
    re.I,
)
_NUMBER = re.compile(r"\d+")

_INSTRUCTION = (
    "You translate a customer-support reply into {language}. Translate faithfully and "
    "naturally, as a friendly support agent would write it. Do not add, remove, or change any "
    "fact, promise, number, order or ticket id, amount, date, or status meaning. Keep '#' "
    'before ids. In Turkish, a support ticket is "destek talebi" and a refund is "iade". '
    "Return only the translated reply."
)


def detect_language(message: str) -> str | None:
    """The customer's language when it is one we translate into, else None (English)."""

    if _TURKISH_LETTERS.search(message) or _TURKISH_WORDS.search(message):
        return "Turkish"
    return None


def numbers_preserved(source: str, translated: str) -> bool:
    return sorted(_NUMBER.findall(source)) == sorted(_NUMBER.findall(translated))


class ResponseLocalizer(Protocol):
    def translate(self, reply: str, language: str) -> str: ...


class OpenAICompatibleLocalizer:
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

    def translate(self, reply: str, language: str) -> str:
        result = self._model.invoke(
            [
                SystemMessage(content=_INSTRUCTION.format(language=language)),
                HumanMessage(content=reply),
            ]
        )
        content = result.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return str(content).strip()


_ENGLISH_WORDS = re.compile(
    r"\b(?:the|is|are|my|me|you|what|where|when|how|why|can|please|order|refund|hi|hello|"
    r"thanks|want|need|help|yes|no|ok|okay|bye|i|remember|your|this|that|with|for|and|of|to|"
    r"over|from|have|has|was|will|would|could|should|not|don't|can't|it|its|we|our)\b",
    re.I,
)


# Default for localize_reply: detect the language from the customer's message.
DETECT = "detect"


def conversation_language(user_messages: list[str]) -> str | None:
    """The language of the latest message that shows one; "2" or "#5" keep the previous one."""

    for message in reversed(user_messages):
        if detect_language(message):
            return detect_language(message)
        if _ENGLISH_WORDS.search(message):
            return None
    return None


def localize_reply(
    localizer: ResponseLocalizer | None,
    customer_message: str,
    reply: str,
    *,
    language: str | None = DETECT,
) -> str:
    """Translate into `language` (None keeps English); by default detect it from the message."""

    if language == DETECT:
        language = detect_language(customer_message)
    if localizer is None or language is None or not reply.strip():
        return reply
    # Quoted customer text (a ticket description) does not make the reply itself Turkish, and a
    # reply that still holds English words is translated whole, never sent half and half.
    unquoted = re.sub(r'"[^"]*"', "", reply)
    if detect_language(unquoted) == language and not _ENGLISH_WORDS.search(unquoted):
        return reply  # Already written in the customer's language.
    try:
        translated = localizer.translate(reply, language)
    except Exception as error:  # noqa: BLE001 - translation is best effort
        logger.warning(
            "Reply translation failed.", extra={"translation_error_type": type(error).__name__}
        )
        return reply
    if not translated or not numbers_preserved(reply, translated):
        logger.warning("Reply translation rejected.", extra={"translation_language": language})
        return reply
    return translated
