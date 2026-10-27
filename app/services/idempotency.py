from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import BusinessActionReceipt
from app.observability.metrics import get_metrics
from app.resilience.errors import UnknownWriteOutcomeError
from app.tools.base import DuplicateActionError


class IdempotencyScope(BaseModel):
    """Server-owned identity for one retryable business request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=8, max_length=200)
    tenant_id: str = Field(default="default", min_length=1, max_length=200)


def execute_idempotent[T](
    session: Session,
    *,
    scope: IdempotencyScope,
    operation: str,
    customer_id: int,
    request_payload: Mapping[str, Any],
    perform: Callable[[], tuple[T, int]],
    load_result: Callable[[int], T],
) -> T:
    """Execute and receipt a write in one transaction, or return its prior result."""

    fingerprint = _request_fingerprint(request_payload)
    existing = _get_receipt(session, scope, operation)
    if existing is not None:
        _validate_receipt(existing, customer_id, fingerprint)
        return load_result(existing.result_id)
    try:
        with session.begin_nested():
            result, result_id = perform()
            session.add(
                BusinessActionReceipt(
                    tenant_id=scope.tenant_id,
                    actor_id=scope.actor_id,
                    operation=operation,
                    idempotency_key=scope.key,
                    customer_id=customer_id,
                    request_fingerprint=fingerprint,
                    result_id=result_id,
                )
            )
            session.flush()
        return result
    except (IntegrityError, DuplicateActionError):
        # A serialized writer may detect the prior effect before reaching INSERT.
        # Replay only if this exact request has a committed receipt.
        existing = _get_receipt(session, scope, operation)
        if existing is None:
            raise
        _validate_receipt(existing, customer_id, fingerprint)
        return load_result(existing.result_id)


def commit_business_write(session: Session, operation: str) -> None:
    """Commit once and classify connection loss/timeouts as an unknown write outcome."""

    try:
        session.commit()
    except (TimeoutError, OperationalError) as error:
        raise UnknownWriteOutcomeError(operation) from error
    except DBAPIError as error:
        if error.connection_invalidated:
            raise UnknownWriteOutcomeError(operation) from error
        raise


def _get_receipt(
    session: Session, scope: IdempotencyScope, operation: str
) -> BusinessActionReceipt | None:
    started = time.perf_counter()
    status = "error"
    try:
        receipt = session.scalar(
            select(BusinessActionReceipt).where(
                BusinessActionReceipt.actor_id == scope.actor_id,
                BusinessActionReceipt.tenant_id == scope.tenant_id,
                BusinessActionReceipt.operation == operation,
                BusinessActionReceipt.idempotency_key == scope.key,
            )
        )
        status = "hit" if receipt is not None else "miss"
        return receipt
    finally:
        get_metrics().idempotency_lookup_duration_seconds.record(
            time.perf_counter() - started, {"status": status}
        )


def _validate_receipt(receipt: BusinessActionReceipt, customer_id: int, fingerprint: str) -> None:
    if receipt.customer_id != customer_id or receipt.request_fingerprint != fingerprint:
        raise DuplicateActionError("Idempotency key was already used for a different request")


def _request_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
