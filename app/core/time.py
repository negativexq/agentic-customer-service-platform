"""Clock helpers for the existing database timestamp contract."""

from datetime import UTC, datetime


def utc_now_naive() -> datetime:
    """Return UTC wall time for timestamp-without-time-zone database columns.

    Persisted timestamps and memory TTL comparisons currently use naive UTC.
    Read the aware UTC clock, then remove only the timezone label so defaults
    remain compatible without a schema or serialization migration.
    """
    return datetime.now(UTC).replace(tzinfo=None)
