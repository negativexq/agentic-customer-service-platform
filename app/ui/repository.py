from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Protocol

from sqlalchemy import Select, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import AgentRunProjectionRecord
from app.ui.schemas import AgentRunView

DEFAULT_PROJECTION_QUERY_LIMIT = 50
MAX_PROJECTION_QUERY_LIMIT = 100
DEFAULT_PROJECTION_MEMORY_LIMIT = 500
_DECISION_EVIDENCE_KEY = "_operator_decision_evidence"
_DECISION_REASON_KEY = "_operator_decision_reason"
_MEMORY_USAGE_KEY = "_operator_memory_usage"
_EXECUTION_METADATA_KEY = "_operator_execution_metadata"
_PROPOSAL_KEY = "_operator_proposal"
_ANSWER_GROUNDING_KEY = "_operator_answer_grounding"
_SECURITY_SIGNAL_KEY = "_operator_security_signal"
_OPERATION_TYPE_KEY = "_operator_operation_type"


class AgentRunProjectionRepository(Protocol):
    """Observational operator read-model storage; never an execution authority."""

    def upsert(self, projection: AgentRunView) -> None: ...

    def get_by_run_id(self, run_id: str, *, tenant_id: str = "default") -> AgentRunView | None: ...

    def list_recent(
        self,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
        customer_ids: Sequence[int] | None = None,
    ) -> list[AgentRunView]: ...

    def list_for_customer(
        self,
        customer_id: int,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
    ) -> list[AgentRunView]: ...

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
        customer_ids: Sequence[int] | None = None,
    ) -> list[AgentRunView]: ...


class InMemoryAgentRunProjectionRepository:
    """Bounded, thread-safe, non-production projection adapter for tests/local use."""

    def __init__(self, max_projections: int = DEFAULT_PROJECTION_MEMORY_LIMIT) -> None:
        if max_projections < 1:
            raise ValueError("max_projections must be positive")
        self.max_projections = max_projections
        self._lock = RLock()
        self._projections: dict[str, AgentRunView] = {}

    def upsert(self, projection: AgentRunView) -> None:
        with self._lock:
            existing = self._projections.get(projection.run_id)
            if existing is not None:
                projection = projection.model_copy(update={"started_at": existing.started_at})
            self._projections[projection.run_id] = projection
            while len(self._projections) > self.max_projections:
                oldest_run_id = min(
                    self._projections,
                    key=lambda run_id: (
                        self._projections[run_id].started_at,
                        run_id,
                    ),
                )
                del self._projections[oldest_run_id]

    def get_by_run_id(self, run_id: str, *, tenant_id: str = "default") -> AgentRunView | None:
        with self._lock:
            projection = self._projections.get(run_id)
            return (
                projection if projection is not None and projection.tenant_id == tenant_id else None
            )

    def list_recent(
        self,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
        customer_ids: Sequence[int] | None = None,
    ) -> list[AgentRunView]:
        with self._lock:
            return _sort_recent(
                [
                    item
                    for item in self._projections.values()
                    if item.tenant_id == tenant_id
                    and (customer_ids is None or item.customer_id in customer_ids)
                ],
                limit,
            )

    def list_for_customer(
        self,
        customer_id: int,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
    ) -> list[AgentRunView]:
        with self._lock:
            return _sort_recent(
                [
                    projection
                    for projection in self._projections.values()
                    if projection.customer_id == customer_id and projection.tenant_id == tenant_id
                ],
                limit,
            )

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
        customer_ids: Sequence[int] | None = None,
    ) -> list[AgentRunView]:
        with self._lock:
            matching = [
                projection
                for projection in self._projections.values()
                if projection.conversation_id == conversation_id
                and projection.tenant_id == tenant_id
                and (customer_ids is None or projection.customer_id in customer_ids)
            ]
            return _sort_recent(matching, limit)[::-1]


class SqlAlchemyAgentRunProjectionRepository:
    """PostgreSQL/SQLAlchemy projection repository with bounded deterministic reads."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, projection: AgentRunView) -> None:
        record = self.session.scalar(
            select(AgentRunProjectionRecord).where(
                AgentRunProjectionRecord.run_id == projection.run_id,
                AgentRunProjectionRecord.tenant_id == projection.tenant_id,
            )
        )
        try:
            if record is None:
                self.session.add(_to_record(projection))
            else:
                _update_record(record, projection)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            record = self.session.scalar(
                select(AgentRunProjectionRecord).where(
                    AgentRunProjectionRecord.run_id == projection.run_id,
                    AgentRunProjectionRecord.tenant_id == projection.tenant_id,
                )
            )
            if record is None:
                raise
            _update_record(record, projection)
            self.session.commit()

    def get_by_run_id(self, run_id: str, *, tenant_id: str = "default") -> AgentRunView | None:
        record = self.session.scalar(
            select(AgentRunProjectionRecord).where(
                AgentRunProjectionRecord.run_id == run_id,
                AgentRunProjectionRecord.tenant_id == tenant_id,
            )
        )
        return _from_record(record) if record is not None else None

    def list_recent(
        self,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
        customer_ids: Sequence[int] | None = None,
    ) -> list[AgentRunView]:
        return self._list(
            select(AgentRunProjectionRecord).where(
                AgentRunProjectionRecord.tenant_id == tenant_id,
                true()
                if customer_ids is None
                else AgentRunProjectionRecord.effective_customer_id.in_(customer_ids),
            ),
            limit,
            newest_first=True,
        )

    def list_for_customer(
        self,
        customer_id: int,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
    ) -> list[AgentRunView]:
        return self._list(
            select(AgentRunProjectionRecord).where(
                AgentRunProjectionRecord.effective_customer_id == customer_id,
                AgentRunProjectionRecord.tenant_id == tenant_id,
            ),
            limit,
            newest_first=True,
        )

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        tenant_id: str = "default",
        limit: int = DEFAULT_PROJECTION_QUERY_LIMIT,
        customer_ids: Sequence[int] | None = None,
    ) -> list[AgentRunView]:
        return self._list(
            select(AgentRunProjectionRecord).where(
                AgentRunProjectionRecord.conversation_id == conversation_id,
                AgentRunProjectionRecord.tenant_id == tenant_id,
                true()
                if customer_ids is None
                else AgentRunProjectionRecord.effective_customer_id.in_(customer_ids),
            ),
            limit,
            newest_first=False,
        )

    def _list(
        self,
        statement: Select[tuple[AgentRunProjectionRecord]],
        limit: int,
        *,
        newest_first: bool,
    ) -> list[AgentRunView]:
        bounded = _bounded_limit(limit)
        ordered = statement.order_by(
            AgentRunProjectionRecord.created_at.desc(),
            AgentRunProjectionRecord.id.desc(),
        )
        rows = list(self.session.scalars(ordered.limit(bounded)))
        if not newest_first:
            rows.reverse()
        return [_from_record(row) for row in rows]


def build_agent_run_projection_repository(
    settings: object, session: Session | None = None
) -> AgentRunProjectionRepository:
    backend = getattr(settings, "agent_run_projection_backend", "memory")
    if backend == "memory":
        from app.ui.projection import get_projection_store

        return get_projection_store()
    if session is None:
        raise ValueError("A database session is required for PostgreSQL projections.")
    return SqlAlchemyAgentRunProjectionRepository(session)


def _to_record(projection: AgentRunView) -> AgentRunProjectionRecord:
    payload = projection.model_dump(mode="json")
    retrieval_metadata = dict(payload["retrieval_metadata"])
    # Keep the frozen Alembic head intact. These bounded operator fields are stored
    # in the existing JSON projection envelope for backward-compatible reconstruction.
    retrieval_metadata[_DECISION_EVIDENCE_KEY] = payload["evidence"]
    retrieval_metadata[_DECISION_REASON_KEY] = payload["decision_reason"]
    retrieval_metadata[_MEMORY_USAGE_KEY] = payload["memory"]
    retrieval_metadata[_EXECUTION_METADATA_KEY] = {
        "execution_mode": payload["execution_mode"],
        "provider": payload["provider"],
        "model": payload["model"],
        "fallback_message": payload["fallback_message"],
        "provider_metadata": payload["provider_metadata"],
    }
    retrieval_metadata[_PROPOSAL_KEY] = payload["proposal"]
    retrieval_metadata[_ANSWER_GROUNDING_KEY] = payload["answer_grounding"]
    retrieval_metadata[_SECURITY_SIGNAL_KEY] = payload["security_signal"]
    retrieval_metadata[_OPERATION_TYPE_KEY] = payload["operation_type"]
    return AgentRunProjectionRecord(
        run_id=projection.run_id,
        request_id=projection.request_id,
        conversation_id=projection.conversation_id,
        tenant_id=projection.tenant_id,
        action_id=projection.action_id,
        effective_customer_id=projection.customer_id,
        actor_id=projection.actor_id,
        actor_type=projection.actor_type,
        roles=payload["roles"],
        intent=projection.intent,
        request_type=projection.request_type,
        status=projection.status,
        started_at=projection.started_at,
        duration_ms=projection.duration_ms,
        trace_id=projection.trace_id,
        path=payload["path"],
        failure_category=projection.failure_category,
        degraded_components=payload["degraded_components"],
        recovery_action=projection.recovery_action,
        memory_item_count=projection.memory.item_count,
        memory_keys=payload["memory"]["keys"],
        memory_types=payload["memory"]["types"],
        tools=payload["tools"],
        policy=payload["policy"],
        rag_documents=payload["rag_documents"],
        retrieval_metadata=retrieval_metadata,
        trace=payload["trace"],
        created_at=projection.started_at,
        updated_at=datetime.now(UTC),
    )


def _update_record(record: AgentRunProjectionRecord, projection: AgentRunView) -> None:
    replacement = _to_record(projection)
    stored_updated_at = record.updated_at
    if stored_updated_at.tzinfo is None:
        stored_updated_at = stored_updated_at.replace(tzinfo=UTC)
    if replacement.updated_at <= stored_updated_at:
        replacement.updated_at = stored_updated_at + timedelta(microseconds=1)
    for field in (
        "request_id",
        "conversation_id",
        "tenant_id",
        "action_id",
        "effective_customer_id",
        "actor_id",
        "actor_type",
        "roles",
        "intent",
        "request_type",
        "status",
        "duration_ms",
        "trace_id",
        "path",
        "failure_category",
        "degraded_components",
        "recovery_action",
        "memory_item_count",
        "memory_keys",
        "memory_types",
        "tools",
        "policy",
        "rag_documents",
        "retrieval_metadata",
        "trace",
        "updated_at",
    ):
        setattr(record, field, getattr(replacement, field))


def _from_record(record: AgentRunProjectionRecord) -> AgentRunView:
    stored_retrieval_metadata = dict(record.retrieval_metadata or {})
    decision_evidence = stored_retrieval_metadata.pop(_DECISION_EVIDENCE_KEY, None)
    decision_reason = stored_retrieval_metadata.pop(_DECISION_REASON_KEY, None)
    memory_usage = stored_retrieval_metadata.pop(_MEMORY_USAGE_KEY, None)
    raw_execution_metadata = stored_retrieval_metadata.pop(_EXECUTION_METADATA_KEY, None)
    execution_metadata = raw_execution_metadata if isinstance(raw_execution_metadata, dict) else {}
    proposal = stored_retrieval_metadata.pop(_PROPOSAL_KEY, None)
    answer_grounding = stored_retrieval_metadata.pop(_ANSWER_GROUNDING_KEY, None)
    security_signal = stored_retrieval_metadata.pop(_SECURITY_SIGNAL_KEY, None)
    operation_type = stored_retrieval_metadata.pop(_OPERATION_TYPE_KEY, "agent_request")
    return AgentRunView.model_validate(
        {
            "run_id": record.run_id,
            "request_id": record.request_id,
            "conversation_id": record.conversation_id,
            "tenant_id": record.tenant_id,
            "action_id": record.action_id,
            "customer_id": record.effective_customer_id,
            "actor_id": record.actor_id,
            "actor_type": record.actor_type,
            "roles": record.roles,
            "intent": record.intent,
            "request_type": record.request_type,
            "operation_type": operation_type,
            "status": record.status,
            "started_at": record.started_at,
            "duration_ms": record.duration_ms,
            "trace_id": record.trace_id,
            "path": record.path,
            "failure_category": record.failure_category,
            "degraded_components": record.degraded_components,
            "recovery_action": record.recovery_action,
            "memory": memory_usage
            or {
                "item_count": record.memory_item_count,
                "keys": record.memory_keys,
                "types": record.memory_types,
            },
            "tools": record.tools,
            "policy": record.policy,
            "rag_documents": record.rag_documents,
            "retrieval_metadata": stored_retrieval_metadata,
            "answer_grounding": answer_grounding,
            "trace": record.trace,
            "decision_reason": decision_reason,
            "evidence": decision_evidence,
            "execution_mode": execution_metadata.get("execution_mode", "recorded_replay"),
            "provider": execution_metadata.get("provider", "recorded_evidence"),
            "model": execution_metadata.get("model"),
            "fallback_message": execution_metadata.get("fallback_message"),
            "provider_metadata": execution_metadata.get("provider_metadata"),
            "proposal": proposal,
            "security_signal": security_signal,
        }
    )


def _bounded_limit(limit: int) -> int:
    return min(max(limit, 1), MAX_PROJECTION_QUERY_LIMIT)


def _sort_recent(projections: Sequence[AgentRunView], limit: int) -> list[AgentRunView]:
    bounded = _bounded_limit(limit)
    return sorted(
        projections,
        key=lambda projection: (projection.started_at, projection.run_id),
        reverse=True,
    )[:bounded]
