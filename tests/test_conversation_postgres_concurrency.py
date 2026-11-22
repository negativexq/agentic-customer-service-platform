"""Human handoff lock ordering; set TEST_POSTGRES_URL to run against PostgreSQL.

Each test owns a temporary schema; no application tables are modified.
"""

import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.models import ActorType, Principal
from app.core.context import ExecutionContext
from app.core.database import Base
from app.models import Conversation, ConversationOwner, Customer, Escalation, EscalationPriority
from app.models.entities import EscalationStatus, Tenant
from app.services.conversations import (
    ConversationOwnershipChangedError,
    begin_agent_turn,
    claim_conversation,
    lock_current_agent_turn,
    queue_for_human,
)

CONVERSATION_ID = "postgres-handoff"


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for PostgreSQL concurrency tests")
    admin = create_engine(url)
    assert admin.dialect.name == "postgresql"
    schema = f"handoff_test_{uuid4().hex}"
    engine = create_engine(
        url,
        connect_args={"options": f"-c search_path={schema} -c statement_timeout=10000"},
    )
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Tenant(id="default", name="Handoff concurrency test"))
            session.flush()
            session.add(Customer(id=1, name="Test", email="test@example.com"))
            session.commit()
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _context() -> ExecutionContext:
    return ExecutionContext(
        request_id=str(uuid4()),
        conversation_id=CONVERSATION_ID,
        principal=Principal(
            actor_id="customer-1",
            actor_type=ActorType.CUSTOMER,
            roles=["customer"],
            customer_id=1,
        ),
        effective_customer_id=1,
    )


def _add_escalation(session: Session) -> int:
    escalation = Escalation(
        customer_id=1,
        reason="customer requested",
        priority=EscalationPriority.HIGH,
        summary="Customer asked for a person",
        status=EscalationStatus.QUEUED,
    )
    session.add(escalation)
    session.flush()
    return escalation.id


def _queued_conversation(engine: Engine) -> tuple[int, int]:
    with Session(engine) as session:
        begin_agent_turn(session, _context())
        conversation = lock_current_agent_turn(session, _context(), 0)
        queue_for_human(
            session,
            conversation,
            escalation_id=_add_escalation(session),
            actor_id="customer-1",
        )
        result = conversation.id, conversation.ownership_version
        session.commit()
        return result


def _wait_for_block(engine: Engine, waiter: int, blocker: int) -> None:
    """Advance only once PostgreSQL confirms the intended lock dependency."""
    deadline = monotonic() + 5
    with engine.connect() as connection:
        while monotonic() < deadline:
            pids = connection.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter})
            if blocker in pids:
                return
    raise AssertionError(f"Backend {waiter} did not block on {blocker}")


def _run_blocked(
    engine: Engine, holder: Session, blocked: Callable[[Session], None], *, ready: Event
) -> None:
    """Run ``blocked`` in a second transaction until it waits on ``holder``, then commit."""

    sessions = sessionmaker(engine, autoflush=False)
    pids: list[int] = []

    def worker() -> None:
        with sessions() as session:
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            blocked(session)
            session.rollback()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker)
        assert ready.wait(5)
        blocker = holder.scalar(text("SELECT pg_backend_pid()"))
        try:
            _wait_for_block(engine, pids[0], blocker)
        finally:
            holder.commit()
        future.result(timeout=10)


def test_write_waits_for_escalation_then_refuses_stale_turn(pg_engine: Engine) -> None:
    with Session(pg_engine) as session:
        turn = begin_agent_turn(session, _context())

    def stale_write(session: Session) -> None:
        with pytest.raises(ConversationOwnershipChangedError):
            lock_current_agent_turn(session, _context(), turn.version)

    with Session(pg_engine) as escalating:
        conversation = lock_current_agent_turn(escalating, _context(), turn.version)
        queue_for_human(
            escalating,
            conversation,
            escalation_id=_add_escalation(escalating),
            actor_id="customer-1",
        )
        _run_blocked(pg_engine, escalating, stale_write, ready=Event())


def test_concurrent_claims_produce_exactly_one_owner(pg_engine: Engine) -> None:
    conversation_pk, version = _queued_conversation(pg_engine)

    def losing_claim(session: Session) -> None:
        with pytest.raises(ConversationOwnershipChangedError):
            claim_conversation(
                session,
                tenant_id="default",
                conversation_pk=conversation_pk,
                actor_id="operator-b",
                expected_version=version,
            )

    with Session(pg_engine) as winner:
        claim_conversation(
            winner,
            tenant_id="default",
            conversation_pk=conversation_pk,
            actor_id="operator-a",
            expected_version=version,
        )
        _run_blocked(pg_engine, winner, losing_claim, ready=Event())

    with Session(pg_engine) as session:
        conversation = session.get(Conversation, conversation_pk)
        assert conversation is not None
        assert conversation.owner == ConversationOwner.HUMAN
        assert conversation.assignee_actor_id == "operator-a"


def test_concurrent_first_turns_share_one_conversation(pg_engine: Engine) -> None:
    def first_turn(_: int) -> int:
        with Session(pg_engine) as session:
            return begin_agent_turn(session, _context()).version

    with ThreadPoolExecutor(max_workers=8) as executor:
        versions = list(executor.map(first_turn, range(8)))

    assert versions == [0] * 8
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1
