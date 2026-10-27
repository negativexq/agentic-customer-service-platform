# Refund creation concurrency

Issue #13 was reproduced against `dbf52feedc61fbfa38216373133439f48cee11c0`
on PostgreSQL 16.14 at `READ COMMITTED`: transaction A paused after validating
`delivered`, transaction B committed a direct update to `cancelled`, then A
successfully committed a refund request. The lock-lifetime regression below
also fails against that original implementation and passes with the fix.

Refund creation requires the authoritative order status to be `delivered` from
eligibility validation through the transaction's commit. The mutation reads the
tenant-scoped order with `SELECT FOR UPDATE` and `populate_existing=True`, so an
order already cached in the SQLAlchemy session cannot supply stale eligibility.
Preflight validation does not acquire this lock; the mutation validates again.
The caller must commit or roll back the outer transaction; releasing the
idempotency savepoint does not release the successful mutation's order lock.

At PostgreSQL's default `READ COMMITTED` isolation, an earlier order update makes
refund creation wait, then validate the committed status. If the refund obtains
the lock first, an external order update waits until the refund transaction ends.
Stronger isolation levels may instead reject conflicting transactions with a
serialization error; callers must not treat that as a successful refund.

This is a creation-time invariant. It does not prevent an external writer from
changing order status after refund creation commits. Any future lifecycle writer
that allows transitions away from `delivered` must define its treatment of active
refunds and enforce that rule while holding the same order lock. The current
application status writer is cancellation (`app/tools/orders.py`): it locks the
order and only accepts `pending`, `processing`, or already `cancelled`. Therefore
the current API cannot perform the incompatible delivered-to-cancelled transition
used by the regression tests; that transition models an external database writer.

The active-refund partial unique index remains in place. Refund and idempotency
receipt are still written in one transaction. A concurrent request with the same
key replays the committed receipt even if it first encounters the active-refund
validation error after waiting for the order lock. A different key is rejected.

Run the deterministic PostgreSQL regressions against a test database:

```sh
TEST_POSTGRES_URL=postgresql+psycopg://user:password@localhost/test_db \
  uv run pytest tests/test_refund_postgres_concurrency.py
```

Tests create and remove a unique schema per test and require schema creation
permission. They use independent sessions and PostgreSQL's `pg_blocking_pids()`
to establish lock ordering, rather than sleeps. CI provides PostgreSQL 16 and runs
these alongside the existing suite. Without `TEST_POSTGRES_URL`, they are skipped.
