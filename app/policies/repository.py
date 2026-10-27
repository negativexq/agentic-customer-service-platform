from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import Select, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import PolicyAuditRecord
from app.policies.models import PolicyAuditEvent
from app.resilience.errors import AuditPersistenceError

DEFAULT_AUDIT_QUERY_LIMIT = 50
MAX_AUDIT_QUERY_LIMIT = 100


def append_policy_audit(repository: PolicyAuditRepository, event: PolicyAuditEvent) -> None:
    """Convert repository failures to a safe dependency error at the audit boundary."""

    try:
        repository.append(event)
    except AuditPersistenceError:
        raise
    except Exception as error:
        raise AuditPersistenceError() from error


class PolicyAuditRepository(Protocol):
    """Durable or explicitly bounded storage for observational policy evidence."""

    def append(self, event: PolicyAuditEvent) -> None: ...

    def list_for_agent_run(
        self,
        agent_run_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
    ) -> list[PolicyAuditEvent]: ...

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
    ) -> list[PolicyAuditEvent]: ...

    def list_for_customer(
        self,
        customer_id: int,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
    ) -> list[PolicyAuditEvent]: ...


class InMemoryPolicyAuditLog:
    """Bounded, non-durable adapter for tests and lightweight local use only."""

    def __init__(self, max_events: int = 500) -> None:
        self._events: deque[PolicyAuditEvent] = deque(maxlen=max_events)

    @property
    def events(self) -> list[PolicyAuditEvent]:
        return list(self._events)

    def append(self, event: PolicyAuditEvent) -> None:
        self._events.append(event)

    def list_for_agent_run(
        self,
        agent_run_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
    ) -> list[PolicyAuditEvent]:
        return _bounded(
            [
                event
                for event in self._events
                if event.agent_run_id == agent_run_id and event.tenant_id == tenant_id
            ],
            limit,
        )

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
    ) -> list[PolicyAuditEvent]:
        return _bounded(
            [
                event
                for event in self._events
                if event.conversation_id == conversation_id and event.tenant_id == tenant_id
            ],
            limit,
        )

    def list_for_customer(
        self,
        customer_id: int,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
    ) -> list[PolicyAuditEvent]:
        return _bounded(
            [
                event
                for event in self._events
                if event.effective_customer_id == customer_id and event.tenant_id == tenant_id
            ],
            limit,
        )


class SqlAlchemyPolicyAuditRepository:
    """PostgreSQL/SQLAlchemy repository with bounded, deterministic reads."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, event: PolicyAuditEvent) -> None:
        existing = self.session.scalar(
            select(PolicyAuditRecord).where(
                PolicyAuditRecord.event_id == event.event_id,
                PolicyAuditRecord.tenant_id == event.tenant_id,
            )
        )
        if existing is not None:
            return
        self.session.add(_to_record(event))
        # Audit is observational and intentionally committed separately from business writes.
        # This makes policy evidence durable before authorization proceeds without claiming
        # atomicity with a later business mutation.
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            if (
                self.session.scalar(
                    select(PolicyAuditRecord).where(
                        PolicyAuditRecord.event_id == event.event_id,
                        PolicyAuditRecord.tenant_id == event.tenant_id,
                    )
                )
                is None
            ):
                raise

    def list_for_agent_run(
        self,
        agent_run_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
        customer_ids: Sequence[int] | None = None,
    ) -> list[PolicyAuditEvent]:
        return self._list(
            select(PolicyAuditRecord).where(
                PolicyAuditRecord.agent_run_id == agent_run_id,
                PolicyAuditRecord.tenant_id == tenant_id,
                true()
                if customer_ids is None
                else PolicyAuditRecord.effective_customer_id.in_(customer_ids),
            ),
            limit,
        )

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
        customer_ids: Sequence[int] | None = None,
    ) -> list[PolicyAuditEvent]:
        return self._list(
            select(PolicyAuditRecord).where(
                PolicyAuditRecord.conversation_id == conversation_id,
                PolicyAuditRecord.tenant_id == tenant_id,
                true()
                if customer_ids is None
                else PolicyAuditRecord.effective_customer_id.in_(customer_ids),
            ),
            limit,
        )

    def list_for_customer(
        self,
        customer_id: int,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_AUDIT_QUERY_LIMIT,
    ) -> list[PolicyAuditEvent]:
        return self._list(
            select(PolicyAuditRecord).where(
                PolicyAuditRecord.effective_customer_id == customer_id,
                PolicyAuditRecord.tenant_id == tenant_id,
            ),
            limit,
        )

    def _list(
        self, statement: Select[tuple[PolicyAuditRecord]], limit: int
    ) -> list[PolicyAuditEvent]:
        bounded = _bounded_limit(limit)
        rows = list(
            self.session.scalars(
                statement.order_by(
                    PolicyAuditRecord.created_at.desc(), PolicyAuditRecord.id.desc()
                ).limit(bounded)
            )
        )
        rows.reverse()
        return [_from_record(row) for row in rows]


def build_policy_audit_repository(settings: object, session: Session) -> PolicyAuditRepository:
    backend = getattr(settings, "policy_audit_backend", "postgres")
    if backend == "memory":
        return InMemoryPolicyAuditLog(
            max_events=int(getattr(settings, "policy_audit_memory_limit", 500))
        )
    return SqlAlchemyPolicyAuditRepository(session)


def _to_record(event: PolicyAuditEvent) -> PolicyAuditRecord:
    return PolicyAuditRecord(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        agent_run_id=event.agent_run_id,
        request_id=event.request_id,
        conversation_id=event.conversation_id,
        actor_id=event.actor_id,
        actor_type=event.actor_type.value,
        roles=list(event.roles),
        effective_customer_id=event.effective_customer_id,
        action_id=event.action_id,
        tool_name=event.tool_name,
        risk_level=event.risk_level,
        policy_outcome=event.policy_outcome.value,
        reason_codes=list(event.reason_codes)[:10],
        timestamp=event.timestamp,
        stage=event.stage,
        confirmation_status=event.confirmation_status,
        revalidation=event.revalidation,
        execution_status=event.execution_status,
        created_at=event.timestamp,
    )


def _from_record(record: PolicyAuditRecord) -> PolicyAuditEvent:
    from app.auth.models import ActorType
    from app.policies.models import PolicyOutcome

    return PolicyAuditEvent(
        event_id=record.event_id,
        tenant_id=record.tenant_id,
        agent_run_id=record.agent_run_id,
        request_id=record.request_id,
        conversation_id=record.conversation_id,
        actor_id=record.actor_id,
        actor_type=ActorType(record.actor_type),
        roles=list(record.roles),
        effective_customer_id=record.effective_customer_id,
        action_id=record.action_id,
        tool_name=record.tool_name,
        risk_level=record.risk_level,
        policy_outcome=PolicyOutcome(record.policy_outcome),
        reason_codes=list(record.reason_codes),
        timestamp=record.timestamp,
        stage=record.stage,
        confirmation_status=record.confirmation_status,
        revalidation=record.revalidation,
        execution_status=record.execution_status,
    )


def _bounded_limit(limit: int) -> int:
    return min(max(limit, 1), MAX_AUDIT_QUERY_LIMIT)


def _bounded(events: Sequence[PolicyAuditEvent], limit: int) -> list[PolicyAuditEvent]:
    return list(events)[-_bounded_limit(limit) :]
