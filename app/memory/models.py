from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utc_now_naive
from app.memory.schemas import (
    MemoryRedactionState,
    MemoryRetentionPolicy,
    MemorySensitivityLevel,
    MemorySource,
    MemoryStatus,
    MemoryType,
)


class MemoryRecord(Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        Index("ix_memory_records_tenant_customer_status", "tenant_id", "customer_id", "status"),
        Index("ix_memory_records_customer_status", "customer_id", "status"),
        Index("ix_memory_records_customer_key", "customer_id", "normalized_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    memory_type: Mapped[MemoryType] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[MemorySource] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[MemoryStatus] = mapped_column(
        String(20), nullable=False, default=MemoryStatus.ACTIVE
    )
    sensitivity_level: Mapped[MemorySensitivityLevel] = mapped_column(
        String(20), nullable=False, default=MemorySensitivityLevel.INTERNAL
    )
    retention_policy: Mapped[MemoryRetentionPolicy] = mapped_column(
        String(20), nullable=False, default=MemoryRetentionPolicy.STANDARD
    )
    redaction_state: Mapped[MemoryRedactionState] = mapped_column(
        String(20), nullable=False, default=MemoryRedactionState.NOT_REQUIRED
    )
