import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, tzinfo
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.database import Base
from app.memory.models import MemoryRecord
from app.memory.schemas import MemorySource, MemoryType
from app.memory.service import MemoryService
from app.models.entities import Customer, Tenant


@pytest.fixture(params=["sqlite", "postgres"])
def timestamp_session(request: pytest.FixtureRequest) -> Iterator[Session]:
    url = "sqlite://"
    schema = None
    if request.param == "postgres":
        url = os.getenv("TEST_POSTGRES_URL", "")
        if not url:
            pytest.skip("Set TEST_POSTGRES_URL for PostgreSQL timestamp regressions")
        schema = f"timestamp_regression_{uuid4().hex}"
    engine = create_engine(
        url, **({"connect_args": {"options": "-c timezone=Pacific/Honolulu"}} if schema else {})
    )
    try:
        if schema:
            with engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            bound: Engine = engine.execution_options(schema_translate_map={None: schema})
        else:
            bound = engine
        Base.metadata.create_all(bound)
        with Session(bound) as session:
            yield session
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_database_defaults_updates_and_ttl_keep_naive_utc_contract(
    timestamp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = timestamp_session
    first = datetime(2026, 10, 28, 23, 59, 59, 123456, tzinfo=UTC)
    current = [first]

    class FrozenClock:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            assert tz is UTC  # Never depend on the machine's local timezone.
            return current[0]

    monkeypatch.setattr(clock, "datetime", FrozenClock)
    tenant = Tenant(id="default", name="Timestamp regression")
    session.add(tenant)
    session.flush()
    customer = Customer(id=1, name="Timestamp customer", email="timestamp@example.test")
    session.add(customer)
    session.flush()
    expires = first.replace(tzinfo=None) + timedelta(hours=1)
    memory = MemoryRecord(
        customer_id=customer.id,
        memory_type=MemoryType.PREFERENCE,
        content="The customer prefers email updates.",
        normalized_key="contact_channel",
        source=MemorySource.USER_EXPLICIT,
        expires_at=expires,
    )
    session.add(memory)
    session.commit()
    for record in (tenant, customer, memory):
        session.refresh(record)
        assert record.created_at == first.replace(tzinfo=None)
        assert record.created_at.tzinfo is None
    assert memory.updated_at == memory.created_at
    assert memory.expires_at == expires

    current[0] = first + timedelta(seconds=2)
    memory.content = "The customer prefers concise email updates."
    session.commit()
    session.refresh(memory)
    assert memory.created_at == first.replace(tzinfo=None)
    assert memory.updated_at == current[0].replace(tzinfo=None)
    assert memory.updated_at.tzinfo is None

    # Explicit backfilled dates remain untouched when other records are updated.
    historical = datetime(2020, 1, 1, 12, 30)
    customer.created_at = historical
    session.commit()
    session.expire_all()
    assert customer.created_at == historical

    # Public retrieval/serialization keeps UTC wall time without adding an offset.
    service = MemoryService()
    views = service.retrieve(session, customer.id, "email", now=current[0].replace(tzinfo=None))
    assert len(views) == 1
    view = views[0]
    assert view.model_dump(mode="json")["created_at"] == "2026-10-28T23:59:59.123456"
    assert view.model_dump(mode="json")["updated_at"] == "2026-10-29T00:00:01.123456"
    # Exact expiry remains exclusive and compares against naive UTC dates.
    assert (
        len(
            service.retrieve(session, customer.id, "email", now=expires - timedelta(microseconds=1))
        )
        == 1
    )
    assert service.retrieve(session, customer.id, "email", now=expires) == []
