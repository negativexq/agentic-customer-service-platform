# Backend test warning baseline

Recorded on 2026-10-28 against main baseline `c650a2b` and the warning-cleanup changes in issue [#27](https://github.com/negativexq/agentic-customer-service-platform/issues/27).

## Before

[CI run 40598520240](https://github.com/negativexq/agentic-customer-service-platform/actions/runs/40598520240) recorded **1190 passed, 1 skipped, 11780 warnings**.

| Count | Category / source | Cause |
| --- | --- | --- |
| 11779 | `DeprecationWarning`, emitted through SQLAlchemy defaults | Model `created_at` / memory `updated_at` callbacks called `datetime.utcnow()` once per relevant insert/update. |
| 1 | `StarletteDeprecationWarning`, TestClient import | Starlette fell back to legacy `httpx` because its preferred test transport was absent. |

The volume represents repeated calls to the same deprecated APIs. A stricter resource audit additionally exposed temporary port fixtures that some unit tests left for garbage collection; those were not part of the normally reported 11780 warnings.

## Resolution

- Model defaults and memory `onupdate` use [`utc_now_naive`](../app/core/time.py). It reads `datetime.now(UTC)` and removes the timezone label at the existing persistence boundary. Database columns, existing rows, UTC wall-clock values, and JSON timestamp formats keep their current naive-UTC contract. This is not a timezone/schema migration. [Python's datetime guidance](https://docs.python.org/3.12/library/datetime.html#datetime.datetime.utcnow) explains the deprecated call and aware UTC alternative.
- `httpx2` is a development dependency, locked to 2.16.0. [Starlette 1.6.0's TestClient source](https://github.com/encode/starlette/blob/1.6.0/starlette/testclient.py) explicitly prefers this transport. Production export retains `httpx==0.28.1` / `httpcore==1.0.9`; production SDK/client dependencies were not migrated.
- Runtime smoke scenario validation happens before temporary fixture allocation. Unit tests register real cleanup callbacks before mocking cleanup methods, so assertions and failure-path mocks cannot leak fixture directories.
- Pytest treats `DeprecationWarning`, `StarletteDeprecationWarning`, `ResourceWarning`, and `PytestUnraisableExceptionWarning` as errors. There are no new warning-suppression rules.

## After and reproduction

The full local run with real PostgreSQL enabled and **all warning categories promoted to errors** completed with **1192 passed, 1 skipped, 0 warnings**. The two additional cases are SQLite/PostgreSQL timestamp regressions. The skip is the existing optional production frontend-bundle check when no bundle has been built in the backend workspace.

```sh
uv sync --frozen
TEST_POSTGRES_URL=postgresql+psycopg://... uv run --frozen pytest -W error -ra
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy app tests evaluation scripts
uv run --frozen pip-audit --progress-spinner off
```

The local verification used Python 3.12.13, PostgreSQL 16.14, SQLAlchemy 2.0.51, Starlette 1.6.0, and httpx2 2.16.0. Dependency audit reported no known vulnerabilities. PostgreSQL regressions own a temporary schema and remove it afterward.

[`test_model_timestamps.py`](../tests/test_model_timestamps.py) runs with a deterministic UTC clock across midnight; PostgreSQL connections deliberately use `Pacific/Honolulu`. It checks insert defaults, memory update timestamps, untouched backfilled dates, reload/JSON serialization, and retrieval immediately before and at TTL expiry. The existing full suite covers confirmation, revalidation, idempotency, and concurrent refunds.

This baseline applies to backend pytest warnings. Node deprecation notices emitted by GitHub Actions themselves are separate from this test baseline.
