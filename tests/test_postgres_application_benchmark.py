import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.database import Base
from scripts import benchmark_postgres_application as benchmark


@pytest.mark.parametrize("url", [None, "sqlite://", "mysql://localhost/test"])
def test_postgres_benchmark_never_falls_back_to_sqlite(
    monkeypatch: pytest.MonkeyPatch, url: str | None
) -> None:
    monkeypatch.delenv("CAPACITY_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="PostgreSQL"):
        benchmark.run_postgres_benchmark(database_url=url)


@pytest.mark.parametrize(("operations", "workers"), [(0, 1), (1, 0), (1, 33)])
def test_invalid_workload_configuration_is_rejected(operations: int, workers: int) -> None:
    with pytest.raises(ValueError, match="operations"):
        benchmark.run_postgres_benchmark(operations=operations, workers=workers)


def test_cli_failure_artifact_is_bounded_and_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "failure.json"
    monkeypatch.setenv("CAPACITY_DATABASE_URL", "mysql://user:PRIVATE_SECRET@localhost/database")
    monkeypatch.setattr("sys.argv", ["benchmark", "--output", str(output)])
    assert benchmark.main() == 1
    report = json.loads(output.read_text())
    assert report["passed"] is False and report["error_category"] == "configuration"
    assert "PRIVATE_SECRET" not in output.read_text() + capsys.readouterr().out


@pytest.fixture
def postgres_url() -> str:
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for the real application benchmark regressions")
    return url


def schemas(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return set(
                connection.scalars(
                    text(
                        "SELECT nspname FROM pg_namespace "
                        "WHERE nspname LIKE 'application_benchmark_%'"
                    )
                )
            )
    finally:
        engine.dispose()


def test_real_refund_transactions_produce_receipts_and_latency_evidence(postgres_url: str) -> None:
    before = schemas(postgres_url)
    report = benchmark.run_postgres_benchmark(database_url=postgres_url, operations=12, workers=4)
    assert report["passed"] is True and all(report["invariants"].values())
    assert report["database_backend"] == "postgresql" and report["server_version"]
    assert report["isolation_level"] == "READ COMMITTED"
    assert report["scenarios"]["same_key_replay"]["business_effects"] == 1
    assert report["scenarios"]["same_key_replay"]["receipts"] == 1
    assert report["scenarios"]["different_keys_same_order"]["status_counts"] == {
        "succeeded": 1,
        "duplicate_rejected": 3,
    }
    assert report["scenarios"]["independent_orders"]["business_effects"] == 12
    assert report["scenarios"]["independent_orders"]["receipts"] == 12
    for scenario in report["scenarios"].values():
        assert scenario["attempts_per_second"] > 0
        latency = scenario["latency_ms"]
        assert 0 <= latency["p50"] <= latency["p95"] <= latency["p99"]
    assert postgres_url not in json.dumps(report)
    assert schemas(postgres_url) == before


def test_invariant_failure_produces_nonzero_exit_and_cleans_schema(
    postgres_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = schemas(postgres_url)
    monkeypatch.setenv("CAPACITY_DATABASE_URL", postgres_url)
    monkeypatch.setattr(
        benchmark,
        "_attempt",
        lambda *args: benchmark.Attempt(status="error", duration_ms=1, error_category="database"),
    )
    output = tmp_path / "failed-invariant.json"
    monkeypatch.setattr(
        "sys.argv", ["benchmark", "--operations", "3", "--workers", "2", "--output", str(output)]
    )
    assert benchmark.main() == 1
    report = json.loads(output.read_text())
    assert report["passed"] is False
    assert report["invariants"]["no_unexpected_errors"] is False
    assert schemas(postgres_url) == before


def test_bootstrap_failure_also_cleans_schema(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = schemas(postgres_url)

    def failed_bootstrap(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic bootstrap failure")

    monkeypatch.setattr(Base.metadata, "create_all", failed_bootstrap)
    with pytest.raises(RuntimeError, match="synthetic bootstrap"):
        benchmark.run_postgres_benchmark(database_url=postgres_url, operations=1, workers=1)
    assert schemas(postgres_url) == before
