from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.memory.schemas import MemorySource, MemoryStatus, MemoryType
from app.models import (
    Conversation,
    ConversationOwnershipEvent,
    Customer,
    Escalation,
    MemoryRecord,
    Order,
    RefundRequest,
    SupportTicket,
)
from app.models.entities import OrderStatus, RefundStatus, TicketStatus
from scripts.qa_fixtures import (
    QA_CUSTOMER_IDS,
    QA_FIXTURE_ORDER_IDS,
    QA_MEMORY_SENTINEL,
    QA_TENANT_ID,
)

DEMO_MEMORY_PRIVATE_CONTENT = QA_MEMORY_SENTINEL

# Stable local QA identities are centralized in ``scripts.qa_fixtures`` so the
# seed and reset commands cannot silently drift from the documented scenarios.


def seed_into(session: Session) -> None:
    session.execute(delete(Escalation))
    session.execute(delete(ConversationOwnershipEvent))
    session.execute(delete(Conversation))
    session.execute(delete(RefundRequest))
    session.execute(delete(SupportTicket))
    session.execute(delete(Order))
    session.execute(delete(MemoryRecord))
    session.execute(delete(Customer))
    customer_ids = QA_CUSTOMER_IDS
    order_ids = QA_FIXTURE_ORDER_IDS
    customers = [
        Customer(
            id=customer_ids["refund_success"],
            tenant_id=QA_TENANT_ID,
            name="Ada Lovelace",
            email="ada@example.com",
            created_at=datetime(2026, 1, 1),
        ),
        Customer(
            id=customer_ids["duplicate_refund"],
            tenant_id=QA_TENANT_ID,
            name="Grace Hopper",
            email="grace@example.com",
            created_at=datetime(2026, 1, 2),
        ),
        Customer(
            id=customer_ids["memory_metadata"],
            tenant_id=QA_TENANT_ID,
            name="Alan Turing",
            email="alan@example.com",
            created_at=datetime(2026, 1, 3),
        ),
    ]
    session.add_all(customers)
    session.flush()
    session.add_all(
        [
            Order(
                id=order_ids["refund_candidate"],
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["refund_success"],
                status=OrderStatus.DELIVERED,
                total_amount=Decimal("149.99"),
                created_at=datetime(2026, 1, 10),
            ),
            Order(
                id=order_ids["shipped_order"],
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["refund_success"],
                status=OrderStatus.SHIPPED,
                total_amount=Decimal("39.50"),
                created_at=datetime(2026, 1, 11),
            ),
            Order(
                id=order_ids["duplicate_refund"],
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["duplicate_refund"],
                status=OrderStatus.PROCESSING,
                total_amount=Decimal("89.00"),
                created_at=datetime(2026, 1, 12),
            ),
            Order(
                id=4,
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["duplicate_refund"],
                status=OrderStatus.CANCELLED,
                total_amount=Decimal("210.00"),
                created_at=datetime(2026, 1, 13),
            ),
            Order(
                id=5,
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["memory_metadata"],
                status=OrderStatus.PENDING,
                total_amount=Decimal("24.75"),
                created_at=datetime(2026, 1, 14),
            ),
            Order(
                id=6,
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["memory_metadata"],
                status=OrderStatus.DELIVERED,
                total_amount=Decimal("560.00"),
                created_at=datetime(2026, 1, 15),
            ),
        ]
    )
    session.add(
        MemoryRecord(
            id=1,
            tenant_id=QA_TENANT_ID,
            customer_id=customer_ids["memory_metadata"],
            memory_type=MemoryType.PREFERENCE,
            content=DEMO_MEMORY_PRIVATE_CONTENT,
            normalized_key="response_style",
            source=MemorySource.USER_EXPLICIT,
            confidence=1.0,
            created_at=datetime(2026, 1, 20),
            updated_at=datetime(2026, 1, 20),
            expires_at=datetime(2027, 1, 20),
            status=MemoryStatus.ACTIVE,
        )
    )
    session.add(
        RefundRequest(
            tenant_id=QA_TENANT_ID,
            customer_id=customer_ids["duplicate_refund"],
            order_id=order_ids["duplicate_refund"],
            reason="Existing refund operation for deterministic duplicate protection QA.",
            status=RefundStatus.PROCESSING,
            created_at=datetime(2026, 1, 21),
        )
    )
    session.add_all(
        [
            SupportTicket(
                id=1,
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["refund_success"],
                order_id=order_ids["refund_candidate"],
                category="delivery",
                status=TicketStatus.RESOLVED,
                description="Package arrived later than expected.",
                created_at=datetime(2026, 1, 16),
            ),
            SupportTicket(
                id=2,
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["duplicate_refund"],
                order_id=order_ids["duplicate_refund"],
                category="billing",
                status=TicketStatus.OPEN,
                description="Please explain the charge on my order.",
                created_at=datetime(2026, 1, 17),
            ),
            SupportTicket(
                id=3,
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["duplicate_refund"],
                order_id=None,
                category="account",
                status=TicketStatus.IN_PROGRESS,
                description="I need to update my contact details.",
                created_at=datetime(2026, 1, 18),
            ),
            SupportTicket(
                id=4,
                tenant_id=QA_TENANT_ID,
                customer_id=customer_ids["memory_metadata"],
                order_id=6,
                category="product",
                status=TicketStatus.CLOSED,
                description="The product information was helpful.",
                created_at=datetime(2026, 1, 19),
            ),
        ]
    )


def seed() -> None:
    with SessionLocal.begin() as session:
        seed_into(session)


if __name__ == "__main__":
    seed()
