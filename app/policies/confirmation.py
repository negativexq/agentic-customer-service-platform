import re
from datetime import datetime
from typing import Protocol

from app.core.context import ExecutionContext
from app.policies.models import PendingAction, PendingActionStatus


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now().astimezone()


def parse_confirmation(message: str) -> str:
    # Python's locale-independent casefold maps ASCII I to i. Normalize the two
    # Turkish uppercase forms first so exact, bounded phrase matching remains
    # stable without introducing substring or fuzzy approval behavior.
    normalized = message.translate(str.maketrans("Iİ", "ıi")).casefold()
    normalized = re.sub(r"[,.!?;:]+", " ", normalized)
    normalized = " ".join(normalized.split())
    if normalized in {
        "yes",
        "confirm",
        "confirmed",
        "proceed",
        "do it",
        "yes proceed",
        "yes please",
        "yes please proceed",
        "yes please proceed with the refund",
        "yes please proceed with the refund request",
        "i confirm",
        "ı confirm",
        "proceed with the refund",
        "proceed with the refund request",
        "go ahead",
        "yes go ahead",
        "ok go ahead",
        "yes do it",
        "yes confirm",
        "please continue",
        "approved",
        "evet",
        "onaylıyorum",
        "onayla",
        "devam et",
        "işlemi onaylıyorum",
    }:
        return "confirmed"
    if normalized in {
        "no",
        "cancel",
        "never mind",
        "don't do it",
        "do not do it",
        "don't proceed",
        "do not proceed",
        "no cancel it",
        "stop",
        "hayır",
        "iptal",
        "iptal et",
        "vazgeçtim",
        "onaylamıyorum",
    }:
        return "rejected"
    return "ambiguous"


def is_expired(action: PendingAction, now: datetime, ttl_seconds: int) -> bool:
    return (now - action.created_at).total_seconds() >= ttl_seconds


def transition(action: PendingAction, status: PendingActionStatus) -> PendingAction:
    return action.model_copy(update={"status": status})


def belongs_to_context(action: PendingAction, context: ExecutionContext) -> bool:
    principal = context.principal
    return (
        action.conversation_id == context.conversation_id
        and action.tenant_id == context.tenant_id
        and action.actor_id == principal.actor_id
        and action.actor_type == principal.actor_type
        and action.effective_customer_id == context.effective_customer_id
    )
