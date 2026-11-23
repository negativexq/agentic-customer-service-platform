"""Structured, value-free DLP classification for persistent customer memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.memory.schemas import (
    MemoryCandidate,
    MemoryRedactionState,
    MemoryRetentionPolicy,
    MemorySensitivityLevel,
    MemoryStorageEligibility,
)


class SensitiveDataType(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    CREDIT_CARD = "credit_card"
    IBAN = "iban"
    API_TOKEN = "api_token"
    PASSWORD = "password"
    SECRET = "secret"
    NATIONAL_IDENTIFIER = "national_identifier"
    HEALTHCARE = "healthcare"


@dataclass(frozen=True, slots=True)
class MemoryDLPClassification:
    candidate: MemoryCandidate
    sensitivity_level: MemorySensitivityLevel
    retention_policy: MemoryRetentionPolicy
    storage_eligibility: MemoryStorageEligibility
    redaction_state: MemoryRedactionState


_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)
_API_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{16,})\b"
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)
_PASSWORD = re.compile(
    r"\b(?:password|passwd|passcode|parola|şifre|sifre)"
    r"(?:\s+is\s*|\s*[:=]\s*)\S+",
    re.IGNORECASE,
)
_SECRET = re.compile(
    r"\b(?:secret|api[_ -]?key|access[_ -]?token|client[_ -]?secret|"
    r"gizli[_ -]?anahtar)\b(?:\s*[:=]\s*\S+)?",
    re.IGNORECASE,
)
_NATIONAL_ID = re.compile(
    r"\b(?:ssn|national[_ -]?id|tax[_ -]?id|tckn|tc[_ -]?kimlik|"
    r"kimlik[_ -]?no)\s*[:#=]?\s*\d{6,20}\b",
    re.IGNORECASE,
)
_HEALTHCARE_TERMS = re.compile(
    r"\b(?:diagnosis|diagnosed|prescription|medication|medical|health|doctor|hospital|"
    r"teşhis|teshis|reçete|recete|ilaç|ilac|sağlık|saglik|doktor|hastane)\b",
    re.IGNORECASE,
)

_RESTRICTED_TYPES = {
    SensitiveDataType.CREDIT_CARD,
    SensitiveDataType.IBAN,
    SensitiveDataType.API_TOKEN,
    SensitiveDataType.PASSWORD,
    SensitiveDataType.SECRET,
    SensitiveDataType.NATIONAL_IDENTIFIER,
    SensitiveDataType.HEALTHCARE,
}


def classify_candidate(candidate: MemoryCandidate) -> MemoryDLPClassification:
    """Return a classified candidate without retaining detected values in metadata."""

    content = candidate.content
    detected: list[SensitiveDataType] = []
    if _EMAIL.search(content):
        detected.append(SensitiveDataType.EMAIL)
    if _PHONE.search(content):
        detected.append(SensitiveDataType.PHONE)
    if any(_valid_card(match.group(0)) for match in _CARD.finditer(content)):
        detected.append(SensitiveDataType.CREDIT_CARD)
    if _IBAN.search(content.replace(" ", "")):
        detected.append(SensitiveDataType.IBAN)
    if _API_TOKEN.search(content) or _BEARER.search(content):
        detected.append(SensitiveDataType.API_TOKEN)
    if _PASSWORD.search(content):
        detected.append(SensitiveDataType.PASSWORD)
    if _SECRET.search(content):
        detected.append(SensitiveDataType.SECRET)
    if _NATIONAL_ID.search(content):
        detected.append(SensitiveDataType.NATIONAL_IDENTIFIER)
    if _HEALTHCARE_TERMS.search(content):
        detected.append(SensitiveDataType.HEALTHCARE)

    if any(item in _RESTRICTED_TYPES for item in detected):
        return MemoryDLPClassification(
            candidate=candidate,
            sensitivity_level=MemorySensitivityLevel.RESTRICTED,
            retention_policy=MemoryRetentionPolicy.NO_STORE,
            storage_eligibility=MemoryStorageEligibility.REJECT,
            redaction_state=MemoryRedactionState.REJECTED,
        )
    if detected:
        return MemoryDLPClassification(
            candidate=candidate.model_copy(update={"content": _redact_content(content, detected)}),
            sensitivity_level=MemorySensitivityLevel.SENSITIVE,
            retention_policy=MemoryRetentionPolicy.SHORT,
            storage_eligibility=MemoryStorageEligibility.REDACT,
            redaction_state=MemoryRedactionState.REDACTED,
        )
    return MemoryDLPClassification(
        candidate=candidate,
        sensitivity_level=MemorySensitivityLevel.INTERNAL,
        retention_policy=MemoryRetentionPolicy.STANDARD,
        storage_eligibility=MemoryStorageEligibility.ALLOWED,
        redaction_state=MemoryRedactionState.NOT_REQUIRED,
    )


def detected_types(candidate: MemoryCandidate) -> tuple[SensitiveDataType, ...]:
    """Return category-only DLP evidence for tests and bounded policy reasons."""

    types: list[SensitiveDataType] = []
    content = candidate.content
    if _EMAIL.search(content):
        types.append(SensitiveDataType.EMAIL)
    if _PHONE.search(content):
        types.append(SensitiveDataType.PHONE)
    if any(_valid_card(match.group(0)) for match in _CARD.finditer(content)):
        types.append(SensitiveDataType.CREDIT_CARD)
    if _IBAN.search(content.replace(" ", "")):
        types.append(SensitiveDataType.IBAN)
    if _API_TOKEN.search(content) or _BEARER.search(content):
        types.append(SensitiveDataType.API_TOKEN)
    if _PASSWORD.search(content):
        types.append(SensitiveDataType.PASSWORD)
    if _SECRET.search(content):
        types.append(SensitiveDataType.SECRET)
    if _NATIONAL_ID.search(content):
        types.append(SensitiveDataType.NATIONAL_IDENTIFIER)
    if _HEALTHCARE_TERMS.search(content):
        types.append(SensitiveDataType.HEALTHCARE)
    return tuple(dict.fromkeys(types))


def _redact_content(content: str, detected: list[SensitiveDataType]) -> str:
    redacted = content
    if SensitiveDataType.EMAIL in detected:
        redacted = _EMAIL.sub("[REDACTED_EMAIL]", redacted)
    if SensitiveDataType.PHONE in detected:
        redacted = _PHONE.sub("[REDACTED_PHONE]", redacted)
    return redacted


def _valid_card(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


_MESSAGE_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_BEARER, "[REDACTED_TOKEN]"),
    (_API_TOKEN, "[REDACTED_TOKEN]"),
    (_PASSWORD, "[REDACTED_PASSWORD]"),
    (_SECRET, "[REDACTED_SECRET]"),
    (_NATIONAL_ID, "[REDACTED_NATIONAL_ID]"),
    (_EMAIL, "[REDACTED_EMAIL]"),
)


def redact_message_text(content: str) -> tuple[str, bool]:
    """Redact sensitive values in transcript text; returns the text and whether it changed.

    Unlike memory candidates, a whole message is never dropped: operators need the
    conversation, so only the detected values are replaced by category placeholders.
    """

    redacted = content
    for pattern, placeholder in _MESSAGE_VALUE_PATTERNS:
        redacted = pattern.sub(placeholder, redacted)
    redacted = _CARD.sub(
        lambda match: "[REDACTED_CARD]" if _valid_card(match.group(0)) else match.group(0),
        redacted,
    )
    redacted = _IBAN.sub("[REDACTED_IBAN]", redacted)
    redacted = _PHONE.sub("[REDACTED_PHONE]", redacted)
    return redacted, redacted != content
