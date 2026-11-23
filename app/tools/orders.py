from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Order, OrderStatus, RefundRequest
from app.schemas.domain import OrderResponse
from app.services.idempotency import IdempotencyScope, execute_idempotent
from app.services.order_service import get_order as get_order_record
from app.services.order_service import get_order_for_customer
from app.tools.base import (
    DuplicateActionError,
    InvalidStateTransitionError,
    OwnershipError,
    ResourceNotFoundError,
)


class GetOrderInput(BaseModel):
    order_id: int = Field(gt=0)
    customer_id: int = Field(gt=0)


class CancelOrderInput(BaseModel):
    customer_id: int = Field(gt=0)
    order_id: int = Field(gt=0)


class CancelOrderOutput(BaseModel):
    order_id: int
    customer_id: int
    status: OrderStatus
    changed: bool
    message: str


def get_order(
    session: Session, request: GetOrderInput, *, tenant_id: str = "default"
) -> OrderResponse:
    # Preserve anti-enumeration at HTTP boundaries while allowing the trusted
    # agent/operator projection to distinguish a known foreign-scope resource.
    scoped_order = get_order_record(session, request.order_id, tenant_id)
    if scoped_order is not None and scoped_order.customer_id != request.customer_id:
        raise OwnershipError("Order", request.order_id, request.customer_id)
    order = get_order_for_customer(session, request.order_id, request.customer_id, tenant_id)
    if order is None:
        raise ResourceNotFoundError("Order", request.order_id)
    response = OrderResponse.model_validate(order)
    refund = session.scalar(
        select(RefundRequest)
        .where(RefundRequest.order_id == order.id, RefundRequest.tenant_id == tenant_id)
        .order_by(RefundRequest.created_at.desc(), RefundRequest.id.desc())
        .limit(1)
    )
    if refund is not None:
        response.refund_status = str(refund.status)
    return response


def cancel_order(
    session: Session,
    request: CancelOrderInput,
    *,
    idempotency: IdempotencyScope,
    tenant_id: str = "default",
) -> CancelOrderOutput:
    def load_result(order_id: int) -> CancelOrderOutput:
        order = get_order_record(session, order_id, tenant_id)
        if order is None:
            raise ResourceNotFoundError("Order", order_id)
        return CancelOrderOutput(
            order_id=order.id,
            customer_id=order.customer_id,
            status=order.status,
            changed=False,
            message="Order was already cancelled",
        )

    def perform() -> tuple[CancelOrderOutput, int]:
        order = validate_cancel_order(session, request, lock=True, tenant_id=tenant_id)
        order_status = OrderStatus(order.status)
        if order_status == OrderStatus.CANCELLED:
            return load_result(order.id), order.id
        order.status = OrderStatus.CANCELLED
        session.flush()
        return (
            CancelOrderOutput(
                order_id=order.id,
                customer_id=order.customer_id,
                status=order.status,
                changed=True,
                message="Order cancelled",
            ),
            order.id,
        )

    try:
        return execute_idempotent(
            session,
            scope=idempotency,
            operation="cancel_order",
            customer_id=request.customer_id,
            request_payload=request.model_dump(mode="json"),
            perform=perform,
            load_result=load_result,
        )
    except IntegrityError as error:
        raise DuplicateActionError(
            "Cancellation request conflicts with an existing action"
        ) from error


def validate_cancel_order(
    session: Session,
    request: CancelOrderInput,
    *,
    lock: bool = False,
    tenant_id: str = "default",
) -> Order:
    if lock:
        order = session.scalar(
            select(Order)
            .where(Order.id == request.order_id, Order.tenant_id == tenant_id)
            .with_for_update()
        )
    else:
        order = get_order_record(session, request.order_id, tenant_id)
    if order is None:
        raise ResourceNotFoundError("Order", request.order_id)
    if order.customer_id != request.customer_id:
        raise OwnershipError("Order", request.order_id, request.customer_id)
    order_status = OrderStatus(order.status)
    if order_status not in {
        OrderStatus.PENDING,
        OrderStatus.PROCESSING,
        OrderStatus.CANCELLED,
    }:
        raise InvalidStateTransitionError(
            f"Order {order.id} cannot be cancelled from status {order_status.value}"
        )
    return order
