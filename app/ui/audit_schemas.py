"""Audit trail contracts: mirror frontend/src/features/audit/types.ts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AuditKind = Literal["conversation", "handoff", "operator", "assistant", "action", "safety"]


class AuditActor(BaseModel):
    type: Literal["ai", "operator", "customer", "system"]
    id: str | None = None
    name: str


class AuditEvent(BaseModel):
    id: str
    at: datetime
    kind: AuditKind
    action: str
    actor: AuditActor
    conversation_pk: int
    conversation_id: str
    customer_id: int
    customer_name: str
    summary: str
    detail: str | None = None
    run_id: str | None = None


class AuditLogView(BaseModel):
    total: int
    counts: dict[str, int] = Field(default_factory=dict)
    events: list[AuditEvent] = Field(default_factory=list)
