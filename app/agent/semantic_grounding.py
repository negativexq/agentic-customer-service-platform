"""Deterministic trust-boundary validation for semantic entity references."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.agent.schemas import Intent, SemanticDecision

_DOMAIN_INTEGER_ID = re.compile(r"(?<![A-Za-z0-9_-])([1-9][0-9]*)(?![A-Za-z0-9_-])")


class GroundingStatus(StrEnum):
    GROUNDED = "grounded"
    SYMBOLIC = "symbolic"
    UNGROUNDED = "ungrounded"
    NOT_APPLICABLE = "not_applicable"
    INVALID = "invalid"


class SemanticGrounding(BaseModel):
    """Privacy-safe, server-computed provenance result for a semantic target."""

    model_config = ConfigDict(extra="forbid")

    status: GroundingStatus
    reference_type: Literal["explicit_order", "explicit_ticket", "latest_order"] | None = None
    trusted_source: (
        Literal["current_user_message", "previous_reply_listing", "previous_turn_result"] | None
    ) = None


def _user_domain_ids(user_message: str) -> set[str]:
    """Return canonical integer IDs explicitly present in the current message."""

    return {str(int(value)) for value in _DOMAIN_INTEGER_ID.findall(user_message)}


# "the second one", "ikincisi": a position in the list the assistant just showed.
_ORDINALS = {
    "first": 0,
    "1st": 0,
    "ilk": 0,
    "birinci": 0,
    "second": 1,
    "2nd": 1,
    "ikinci": 1,
    "third": 2,
    "3rd": 2,
    "üçüncü": 2,
    "last": -1,
    "son": -1,
    "sonuncu": -1,
}
_ORDINAL = re.compile(
    r"(?<!\w)(" + "|".join(sorted(_ORDINALS, key=len, reverse=True)) + r")(?:si|sı|su|sü)?(?!\w)",
    re.IGNORECASE,
)
_LISTED_ID = re.compile(r"(?:^|: |; )#([1-9][0-9]*)(?=:| \()")
_READ_ONLY_REFERENCE_INTENTS = frozenset({Intent.ORDER_LOOKUP, Intent.TICKET_LOOKUP})


def _listed_reference(
    user_message: str, previous_reply: str, list_position: int | None = None
) -> str | None:
    """Resolve a list position against the ids the previous reply listed, or nothing.

    The position is the model's reading of the message ("ikincisinin" -> 2) when there is one;
    the fixed ordinal words are the fallback without a model. The id always comes from the
    server's own previous reply.
    """

    listed = _LISTED_ID.findall(previous_reply)
    if len(listed) < 2:
        return None
    if list_position is not None:
        position = len(listed) - 1 if list_position == -1 else list_position - 1
    else:
        ordinals = {
            _ORDINALS[match.casefold()]
            for match in _ORDINAL.findall(user_message.replace("İ", "I"))
        }
        if len(ordinals) != 1:
            return None
        position = ordinals.pop()
    if not 0 <= position < len(listed):
        return None
    return str(int(listed[position]))


def listed_reference(
    user_message: str, previous_reply: str, list_position: int | None = None
) -> int | None:
    """The id at the position the customer named in the list just shown."""

    found = _listed_reference(user_message, previous_reply, list_position)
    return int(found) if found else None


def validate_semantic_grounding(
    decision: SemanticDecision,
    user_message: str,
    previous_reply: str = "",
    last_reference: dict[str, object] | None = None,
    list_position: int | None = None,
) -> SemanticGrounding:
    """Validate semantic target provenance without using model claims or fuzzy matching."""

    target = decision.target
    if target is None:
        return SemanticGrounding(status=GroundingStatus.NOT_APPLICABLE)
    if target.type == "latest_order":
        return SemanticGrounding(
            status=GroundingStatus.SYMBOLIC,
            reference_type=target.type,
        )
    if target.type not in {"explicit_order", "explicit_ticket"}:
        return SemanticGrounding(status=GroundingStatus.INVALID)
    identifier = target.order_id if target.type == "explicit_order" else target.ticket_id
    if identifier is None:
        return SemanticGrounding(
            status=GroundingStatus.INVALID,
            reference_type=target.type,
        )
    if str(identifier) in _user_domain_ids(user_message):
        return SemanticGrounding(
            status=GroundingStatus.GROUNDED,
            reference_type=target.type,
            trusted_source="current_user_message",
        )
    # Read-only questions may point at a row of the list just shown ("where is the second
    # one?"). The position is resolved here, never taken from the model, and writes still
    # need the number typed by the customer.
    if decision.intent in _READ_ONLY_REFERENCE_INTENTS and _listed_reference(
        user_message, previous_reply, list_position
    ) == str(identifier):
        return SemanticGrounding(
            status=GroundingStatus.GROUNDED,
            reference_type=target.type,
            trusted_source="previous_reply_listing",
        )
    # "What was my ticket number?" right after the reply about it: the server recorded which
    # record that reply was about; read-only questions may refer back to it.
    kind = "order" if target.type == "explicit_order" else "ticket"
    if (
        decision.intent in _READ_ONLY_REFERENCE_INTENTS
        and last_reference is not None
        and last_reference.get("kind") == kind
        and last_reference.get("id") == identifier
    ):
        return SemanticGrounding(
            status=GroundingStatus.GROUNDED,
            reference_type=target.type,
            trusted_source="previous_turn_result",
        )
    return SemanticGrounding(status=GroundingStatus.UNGROUNDED, reference_type=target.type)
