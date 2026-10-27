from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Order, OrderStatus, RefundRequest, RefundStatus
from app.schemas.domain import RefundRequestResponse
from app.services.idempotency import IdempotencyScope, execute_idempotent
from app.tools.base import (
    DuplicateActionError,
    InvalidStateTransitionError,
    OwnershipError,
    ResourceNotFoundError,
)


class RequestRefundInput(BaseModel):
    customer_id: int = Field(gt=0)
    order_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)


def request_refund(
    session: Session,
    request: RequestRefundInput,
    *,
    idempotency: IdempotencyScope,
    tenant_id: str = "default",
) -> RefundRequestResponse:
    def load_result(refund_id: int) -> RefundRequestResponse:
        refund = session.scalar(
            select(RefundRequest).where(
                RefundRequest.id == refund_id, RefundRequest.tenant_id == tenant_id
            )
        )
        if refund is None:
            raise ResourceNotFoundError("Refund request", refund_id)
        return RefundRequestResponse.model_validate(refund)

    def perform() -> tuple[RefundRequestResponse, int]:
        validate_refund_request(session, request, lock=True, tenant_id=tenant_id)
        refund = RefundRequest(
            customer_id=request.customer_id,
            tenant_id=tenant_id,
            order_id=request.order_id,
            reason=request.reason,
            status=RefundStatus.REQUESTED,
        )
        session.add(refund)
        session.flush()
        return RefundRequestResponse.model_validate(refund), refund.id

    try:
        return execute_idempotent(
            session,
            scope=idempotency,
            operation="request_refund",
            customer_id=request.customer_id,
            request_payload=request.model_dump(mode="json"),
            perform=perform,
            load_result=load_result,
        )
    except IntegrityError as error:
        raise DuplicateActionError(
            f"An active refund request already exists for order {request.order_id}"
        ) from error


def validate_refund_request(
    session: Session,
    request: RequestRefundInput,
    *,
    lock: bool = False,
    tenant_id: str = "default",
) -> None:
    query = (
        select(Order)
        .where(Order.id == request.order_id, Order.tenant_id == tenant_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        # Keep authoritative eligibility stable until the caller commits or rolls back.
        query = query.with_for_update()
    order = session.scalar(query)
    if order is None:
        raise ResourceNotFoundError("Order", request.order_id)
    if order.customer_id != request.customer_id:
        raise OwnershipError("Order", request.order_id, request.customer_id)
    order_status = OrderStatus(order.status)
    if order_status != OrderStatus.DELIVERED:
        raise InvalidStateTransitionError(
            f"Order {order.id} is not eligible for a refund from status {order_status.value}"
        )
    active_statuses = {
        RefundStatus.REQUESTED,
        RefundStatus.APPROVED,
        RefundStatus.PROCESSING,
    }
    existing = session.scalar(
        select(RefundRequest)
        .where(RefundRequest.order_id == request.order_id)
        .where(RefundRequest.tenant_id == tenant_id)
        .where(RefundRequest.status.in_(active_statuses))
    )
    if existing is not None:
        raise DuplicateActionError(f"An active refund request already exists for order {order.id}")
