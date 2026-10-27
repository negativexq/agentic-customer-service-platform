"""Issue #13 regression tests; set TEST_POSTGRES_URL to run against PostgreSQL.

Each test owns a temporary schema; no application tables are modified.
"""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.models import BusinessActionReceipt, Customer, Order, RefundRequest
from app.models.entities import OrderStatus, Tenant
from app.services.idempotency import IdempotencyScope, commit_business_write
from app.tools.base import DuplicateActionError, InvalidStateTransitionError
from app.tools.refunds import RequestRefundInput, request_refund


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for PostgreSQL concurrency tests")
    admin = create_engine(url)
    assert admin.dialect.name == "postgresql"
    schema = f"refund_test_{uuid4().hex}"
    engine = create_engine(
        url,
        connect_args={"options": f"-c search_path={schema} -c statement_timeout=10000"},
    )
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Tenant(id="default", name="Refund concurrency test"))
            session.flush()
            session.add(Customer(id=1, name="Test", email="test@example.com"))
            session.flush()
            session.add(Order(id=1, customer_id=1, status=OrderStatus.DELIVERED, total_amount=30))
            session.commit()
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def scope(key: str = "refund-test-key") -> IdempotencyScope:
    return IdempotencyScope(actor_id="refund-test", key=key)


def request() -> RequestRefundInput:
    return RequestRefundInput(customer_id=1, order_id=1, reason="Damaged item")


def wait_for_block(engine: Engine, waiter: int, blocker: int) -> None:
    """Advance only once PostgreSQL confirms the intended lock dependency."""
    deadline = monotonic() + 5
    with engine.connect() as connection:
        while monotonic() < deadline:
            pids = connection.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter})
            if blocker in pids:
                return
    raise AssertionError(f"Backend {waiter} did not block on {blocker}")


def test_writer_first_rejects_refund_and_refreshes_cached_order(pg_engine: Engine) -> None:
    sessions = sessionmaker(pg_engine, autoflush=False)
    ready, start = Event(), Event()
    reader_pid: list[int] = []

    def refund_worker() -> None:
        with sessions() as session:
            cached = session.get(Order, 1)
            assert cached is not None and cached.status == OrderStatus.DELIVERED
            reader_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            assert start.wait(5)
            with pytest.raises(InvalidStateTransitionError):
                request_refund(session, request(), idempotency=scope())
            assert str(cached.status) == OrderStatus.CANCELLED
            session.rollback()

    with sessions() as writer, ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(refund_worker)
        assert ready.wait(5)
        blocker = writer.scalar(text("SELECT pg_backend_pid()"))
        writer.execute(update(Order).where(Order.id == 1).values(status=OrderStatus.CANCELLED))
        start.set()
        try:
            wait_for_block(pg_engine, reader_pid[0], blocker)
        finally:
            writer.commit()
        future.result(timeout=10)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(RefundRequest)) == 0
        assert session.scalar(select(func.count()).select_from(BusinessActionReceipt)) == 0


def test_refund_lock_survives_savepoint_until_outer_commit(pg_engine: Engine) -> None:
    sessions = sessionmaker(pg_engine, autoflush=False)
    ready = Event()
    writer_pid: list[int] = []

    def external_writer() -> None:
        with sessions.begin() as session:
            writer_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            session.execute(update(Order).where(Order.id == 1).values(status=OrderStatus.CANCELLED))

    with sessions() as refund_session, ThreadPoolExecutor(max_workers=1) as executor:
        blocker = refund_session.scalar(text("SELECT pg_backend_pid()"))
        result = request_refund(refund_session, request(), idempotency=scope())
        future = executor.submit(external_writer)
        assert ready.wait(5)
        try:
            wait_for_block(pg_engine, writer_pid[0], blocker)
            assert not future.done()
        finally:
            commit_business_write(refund_session, "request_refund")
        future.result(timeout=10)
    with sessions() as session:
        assert session.get(RefundRequest, result.id) is not None
        assert session.scalar(select(func.count()).select_from(BusinessActionReceipt)) == 1
        # The invariant covers creation, not arbitrary external changes after commit.
        order = session.get(Order, 1)
        assert order is not None and order.status == OrderStatus.CANCELLED


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_requests_preserve_replay_and_uniqueness(
    pg_engine: Engine, same_key: bool
) -> None:
    sessions = sessionmaker(pg_engine, autoflush=False)
    ready = Event()
    worker_pid: list[int] = []

    def second_request() -> int | None:
        with sessions() as session:
            worker_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            try:
                result = request_refund(
                    session, request(), idempotency=scope() if same_key else scope("other-key")
                )
                commit_business_write(session, "request_refund")
                return result.id
            except DuplicateActionError:
                session.rollback()
                if same_key:
                    raise
                return None

    with sessions() as first, ThreadPoolExecutor(max_workers=1) as executor:
        blocker = first.scalar(text("SELECT pg_backend_pid()"))
        original = request_refund(first, request(), idempotency=scope())
        future = executor.submit(second_request)
        assert ready.wait(5)
        try:
            wait_for_block(pg_engine, worker_pid[0], blocker)
        finally:
            commit_business_write(first, "request_refund")
        assert future.result(timeout=10) == (original.id if same_key else None)
    with sessions() as session:
        replay = request_refund(session, request(), idempotency=scope())
        assert replay.id == original.id
        assert session.scalar(select(func.count()).select_from(RefundRequest)) == 1
        assert session.scalar(select(func.count()).select_from(BusinessActionReceipt)) == 1
