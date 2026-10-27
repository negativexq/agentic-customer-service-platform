from __future__ import annotations

import hashlib
import logging
import threading
import time
from enum import StrEnum
from math import ceil
from typing import Protocol
from urllib.parse import quote

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.event_hooks import SerdeEvent, register_serde_event_listener
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from app.core.config import Settings, validate_production_checkpoint
from app.core.context import ExecutionContext
from app.observability.metrics import get_metrics

logger = logging.getLogger(__name__)

# Exact application symbols intentionally persisted in AgentState. LangGraph's built-in safe
# types remain independently allowlisted by the checkpoint package. Keep this tuple narrow:
# adding a symbol authorizes its constructor during checkpoint deserialization.
CHECKPOINT_ALLOWED_MSGPACK_TYPES: tuple[tuple[str, str], ...] = (
    # AgentState stores these decision/error enums directly.
    ("app.agent.schemas", "AgentErrorCategory"),
    ("app.agent.schemas", "AgentRequestType"),
    ("app.agent.schemas", "Intent"),
    ("app.agent.schemas", "SemanticDecision"),
    ("app.agent.schemas", "SemanticTarget"),
    ("app.agent.decision_compiler", "CompiledDecision"),
    ("app.agent.decision_compiler", "CompileStatus"),
    # ExecutionContext persists the authenticated principal and its actor enum.
    ("app.auth.models", "ActorType"),
    ("app.auth.models", "Principal"),
    ("app.auth.models", "PrincipalType"),
    ("app.core.context", "ExecutionContext"),
    # Explicit memory requests persist the candidate and memory classification.
    ("app.memory.schemas", "MemoryCandidate"),
    ("app.memory.schemas", "MemoryType"),
    # Risk decisions and durable confirmation state remain typed across restart.
    ("app.policies.models", "PendingAction"),
    ("app.policies.models", "PendingActionStatus"),
    ("app.policies.models", "PolicyDecision"),
    ("app.policies.models", "PolicyOutcome"),
)


class CheckpointDeserializationError(RuntimeError):
    """Raised when checkpoint data requests construction of an unapproved Python type."""


class StrictCheckpointSerializer(JsonPlusSerializer):
    """Make LangGraph's blocked-type signal a fail-closed checkpoint load error."""

    def loads_typed(self, data: tuple[str, bytes]) -> object:
        blocked_events: list[SerdeEvent] = []
        loader_thread_id = threading.get_ident()

        def capture_blocked_event(event: SerdeEvent) -> None:
            if threading.get_ident() == loader_thread_id and event["kind"] in {
                "msgpack_blocked",
                "msgpack_method_blocked",
            }:
                blocked_events.append(event)

        unregister = register_serde_event_listener(capture_blocked_event)
        try:
            value = super().loads_typed(data)
        finally:
            unregister()
        if blocked_events:
            blocked = blocked_events[0]
            symbol = f"{blocked['module']}.{blocked['name']}"
            raise CheckpointDeserializationError(
                f"Checkpoint deserialization rejected unregistered type {symbol}."
            )
        return value


class CheckpointBackend(StrEnum):
    POSTGRES = "postgres"
    MEMORY = "memory"


class CheckpointProvider(Protocol):
    """Application boundary around the LangGraph checkpoint backend lifecycle."""

    @property
    def backend(self) -> CheckpointBackend: ...

    @property
    def checkpointer(self) -> BaseCheckpointSaver[str]: ...

    def initialize(self) -> None: ...

    def is_ready(self) -> bool: ...

    def close(self) -> None: ...


class MemoryCheckpointProvider:
    """Deterministic process-local provider for tests and explicit local use."""

    def __init__(self, checkpointer: MemorySaver | None = None) -> None:
        self._checkpointer = checkpointer or MemorySaver(serde=build_checkpoint_serializer())

    @property
    def backend(self) -> CheckpointBackend:
        return CheckpointBackend.MEMORY

    @property
    def checkpointer(self) -> BaseCheckpointSaver[str]:
        return self._checkpointer

    def initialize(self) -> None:
        return None

    def is_ready(self) -> bool:
        return True

    def close(self) -> None:
        return None


class PostgresCheckpointProvider:
    """Managed official LangGraph PostgreSQL checkpoint provider."""

    def __init__(
        self,
        database_url: str,
        *,
        connect_timeout_seconds: float = 5.0,
        statement_timeout_seconds: float = 10.0,
        pool_timeout_seconds: float = 5.0,
    ) -> None:
        conninfo = _psycopg_conninfo(database_url)
        self._pool: ConnectionPool[Connection[DictRow]] = ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=10,
            open=False,
            timeout=pool_timeout_seconds,
            kwargs={
                "autocommit": True,
                "connect_timeout": ceil(connect_timeout_seconds),
                "options": f"-c statement_timeout={ceil(statement_timeout_seconds * 1000)}",
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self._checkpointer = PostgresSaver(self._pool, serde=build_checkpoint_serializer())
        self._initialized = False

    @property
    def backend(self) -> CheckpointBackend:
        return CheckpointBackend.POSTGRES

    @property
    def checkpointer(self) -> BaseCheckpointSaver[str]:
        return self._checkpointer

    def initialize(self) -> None:
        if self._initialized:
            return
        started = time.perf_counter()
        status = "error"
        try:
            self._pool.open(wait=True)
            self._checkpointer.setup()
            status = "ok"
        except Exception as error:
            self._pool.close()
            get_metrics().checkpoint_write_duration_seconds.record(
                time.perf_counter() - started,
                {"status": status, "backend": self.backend.value},
            )
            logger.error(
                "Checkpoint persistence initialization failed.",
                extra={
                    "checkpoint_backend": self.backend.value,
                    "persistence_error_type": type(error).__name__,
                },
            )
            raise RuntimeError("Checkpoint persistence initialization failed.") from None
        self._initialized = True
        get_metrics().checkpoint_write_duration_seconds.record(
            time.perf_counter() - started, {"status": status, "backend": self.backend.value}
        )

    def is_ready(self) -> bool:
        if not self._initialized:
            return False
        try:
            with self._pool.connection() as connection:
                connection.execute("SELECT 1").fetchone()
        except Exception as error:
            logger.warning(
                "Checkpoint persistence readiness check failed.",
                extra={
                    "checkpoint_backend": self.backend.value,
                    "persistence_error_type": type(error).__name__,
                },
            )
            return False
        return True

    def close(self) -> None:
        if not self._initialized:
            return
        self._pool.close()
        self._initialized = False


def build_checkpoint_provider(settings: Settings) -> CheckpointProvider:
    validate_production_checkpoint(settings)
    backend = CheckpointBackend(settings.checkpoint_backend)
    if backend == CheckpointBackend.MEMORY:
        return MemoryCheckpointProvider()
    return PostgresCheckpointProvider(
        settings.database_url,
        connect_timeout_seconds=settings.database_connect_timeout_seconds,
        statement_timeout_seconds=settings.database_query_timeout_seconds,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
    )


def checkpoint_thread_id(context: ExecutionContext) -> str:
    """Return a collision-safe, actor/customer-scoped LangGraph thread identity."""

    principal = context.principal
    actor_id = quote(principal.actor_id, safe="")
    conversation_id = quote(context.conversation_id, safe="")
    tenant_prefix = (
        "v1" if context.tenant_id == "default" else f"v2:tenant-{quote(context.tenant_id, safe='')}"
    )
    return (
        f"{tenant_prefix}:{principal.actor_type.value}:{actor_id}:"
        f"customer-{context.effective_customer_id}:{conversation_id}"
    )


def checkpoint_thread_id_hash(context: ExecutionContext) -> str:
    """Return a bounded identifier suitable for logs and tracing."""

    value = checkpoint_thread_id(context).encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:16]


def _psycopg_conninfo(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if database_url.startswith("postgres+psycopg://"):
        return database_url.replace("postgres+psycopg://", "postgresql://", 1)
    return database_url


def build_checkpoint_serializer() -> StrictCheckpointSerializer:
    return StrictCheckpointSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=CHECKPOINT_ALLOWED_MSGPACK_TYPES,
    )
