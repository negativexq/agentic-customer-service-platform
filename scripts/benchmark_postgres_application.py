"""Measure real refund/idempotency transactions in an isolated PostgreSQL schema.

CAPACITY_DATABASE_URL is required. This tool never falls back to SQLite.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import BusinessActionReceipt, Customer, Order, RefundRequest
from app.models.entities import OrderStatus, Tenant
from app.services.idempotency import IdempotencyScope, commit_business_write
from app.tools.base import DuplicateActionError
from app.tools.refunds import RequestRefundInput, request_refund


@dataclass(frozen=True)
class Attempt:
    status: str
    duration_ms: float
    result_id: int | None = None
    error_category: str | None = None


def _attempt(engine: Engine, order_id: int, key: str, start: Event) -> Attempt:
    start.wait()
    started = time.perf_counter()
    try:
        with Session(engine, autoflush=False) as session:
            result = request_refund(
                session,
                RequestRefundInput(customer_id=1, order_id=order_id, reason="Synthetic benchmark"),
                tenant_id="benchmark",
                idempotency=IdempotencyScope(tenant_id="benchmark", actor_id="benchmark", key=key),
            )
            commit_business_write(session, "request_refund")
        return Attempt("succeeded", (time.perf_counter() - started) * 1000, result.id)
    except DuplicateActionError:
        return Attempt("duplicate_rejected", (time.perf_counter() - started) * 1000)
    except Exception as error:
        category = "database" if isinstance(error, DBAPIError) else "unexpected"
        return Attempt("error", (time.perf_counter() - started) * 1000, error_category=category)


def _percentile(samples: list[float], quantile: float) -> float:
    position = (len(samples) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(samples) - 1)
    return round(samples[lower] + (samples[upper] - samples[lower]) * (position - lower), 3)


def _workload(engine: Engine, requests: list[tuple[int, str]], workers: int) -> dict[str, Any]:
    start = Event()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_attempt, engine, order, key, start) for order, key in requests]
        start.set()
        attempts = [future.result() for future in futures]
    elapsed = time.perf_counter() - started
    orders = {order for order, _ in requests}
    with Session(engine) as session:
        effects = session.scalar(
            select(func.count())
            .select_from(RefundRequest)
            .where(RefundRequest.tenant_id == "benchmark", RefundRequest.order_id.in_(orders))
        )
        receipts = session.scalar(
            select(func.count())
            .select_from(BusinessActionReceipt)
            .join(RefundRequest, BusinessActionReceipt.result_id == RefundRequest.id)
            .where(
                BusinessActionReceipt.tenant_id == "benchmark",
                BusinessActionReceipt.operation == "request_refund",
                RefundRequest.tenant_id == "benchmark",
                RefundRequest.order_id.in_(orders),
            )
        )
    samples = sorted(attempt.duration_ms for attempt in attempts)
    return {
        "attempts": len(attempts),
        "business_effects": effects,
        "receipts": receipts,
        "unique_result_count": len(
            {attempt.result_id for attempt in attempts if attempt.result_id is not None}
        ),
        "status_counts": dict(Counter(attempt.status for attempt in attempts)),
        "errors": dict(
            Counter(attempt.error_category for attempt in attempts if attempt.error_category)
        ),
        "elapsed_seconds": round(elapsed, 6),
        "attempts_per_second": round(len(attempts) / elapsed, 3),
        "effects_per_second": round((effects or 0) / elapsed, 3),
        "latency_ms": {
            "p50": _percentile(samples, 0.50),
            "p95": _percentile(samples, 0.95),
            "p99": _percentile(samples, 0.99),
            "mean": round(statistics.mean(samples), 3),
        },
    }


def run_postgres_benchmark(
    *, database_url: str | None = None, operations: int = 1000, workers: int = 8
) -> dict[str, Any]:
    if operations < 1 or not 1 <= workers <= 32:
        raise ValueError("operations must be positive and workers must be between 1 and 32")
    url = database_url or os.environ.get("CAPACITY_DATABASE_URL")
    if not url:
        raise ValueError("CAPACITY_DATABASE_URL must point to a dedicated PostgreSQL test database")
    parsed = make_url(url)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("the application benchmark requires PostgreSQL with psycopg")
    # The unqualified PostgreSQL URL uses psycopg too; psycopg2 is not a project dependency.
    engine_url = parsed.set(drivername="postgresql+psycopg")
    schema = f"application_benchmark_{uuid4().hex}"
    admin = create_engine(engine_url, connect_args={"connect_timeout": 5})
    engine = create_engine(
        engine_url,
        pool_size=workers,
        max_overflow=0,
        pool_timeout=10,
        connect_args={
            "connect_timeout": 5,
            "options": f"-c search_path={schema} -c statement_timeout=10000",
        },
    )
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Tenant(id="benchmark", name="Synthetic benchmark"))
            session.flush()
            session.add(
                Customer(
                    id=1, tenant_id="benchmark", name="Synthetic", email="benchmark@example.invalid"
                )
            )
            session.flush()
            session.add_all(
                Order(
                    id=index,
                    tenant_id="benchmark",
                    customer_id=1,
                    status=OrderStatus.DELIVERED,
                    total_amount=30,
                )
                for index in range(1, operations + 3)
            )
            session.commit()
        with engine.connect() as connection:
            version = connection.scalar(text("SHOW server_version"))
            isolation = connection.get_isolation_level()
        same = _workload(engine, [(1, "benchmark-same-key")] * workers, workers)
        competing = _workload(engine, [(2, f"competing-key-{i}") for i in range(workers)], workers)
        independent = _workload(
            engine, [(i + 3, f"independent-key-{i}") for i in range(operations)], workers
        )
        scenarios = {
            "same_key_replay": same,
            "different_keys_same_order": competing,
            "independent_orders": independent,
        }
        invariants = {
            "same_key_one_effect_and_receipt": same["business_effects"]
            == same["receipts"]
            == same["unique_result_count"]
            == 1,
            "same_key_all_succeeded": same["status_counts"].get("succeeded", 0) == workers,
            "competing_keys_one_effect_and_receipt": competing["business_effects"]
            == competing["receipts"]
            == 1,
            "competing_keys_rejected": competing["status_counts"].get("duplicate_rejected", 0)
            == workers - 1,
            "independent_effects_and_receipts": independent["business_effects"]
            == independent["receipts"]
            == independent["unique_result_count"]
            == operations,
            "independent_all_succeeded": independent["status_counts"].get("succeeded", 0)
            == operations,
            "no_unexpected_errors": all(not scenario["errors"] for scenario in scenarios.values()),
        }
        return {
            "schema_version": 1,
            "benchmark": "postgresql_application_refund_idempotency_v1",
            "recorded_at": datetime.now(UTC).isoformat(),
            "database_backend": "postgresql",
            "server_version": version,
            "isolation_level": isolation,
            "operations": operations,
            "workers": workers,
            "pool": {"size": workers, "max_overflow": 0, "timeout_seconds": 10},
            "environment": {
                "python": platform.python_version(),
                "os": platform.system(),
                "architecture": platform.machine(),
                "logical_cpus": os.cpu_count(),
            },
            "scenarios": scenarios,
            "invariants": invariants,
            "passed": all(invariants.values()),
            "privacy": {"raw_user_content": False, "connection_url": False, "provider_calls": 0},
        }
    finally:
        engine.dispose()
        try:
            if created:
                with admin.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            admin.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/benchmarks/postgres-application.json")
    )
    args = parser.parse_args()
    try:
        report = run_postgres_benchmark(operations=args.operations, workers=args.workers)
    except Exception as error:
        # Never print connection URLs, credentials, or driver exception payloads.
        report = {
            "schema_version": 1,
            "benchmark": "postgresql_application_refund_idempotency_v1",
            "passed": False,
            "error_category": "configuration"
            if isinstance(error, ValueError)
            else "database_or_runtime",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True)
    args.output.write_text(payload + "\n")
    print(payload)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
