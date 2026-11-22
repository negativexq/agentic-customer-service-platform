"""Reset the isolated deterministic QA database and seed its known scenarios.

This command is deliberately restricted to non-production environments. It resets
only the QA database backing the current Compose project; it does not change
production startup or runtime behavior.
"""

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    AgentRunProjectionRecord,
    BusinessActionReceipt,
    Conversation,
    ConversationMessage,
    ConversationOwnershipEvent,
    Customer,
    Escalation,
    MemoryRecord,
    Order,
    PolicyAuditRecord,
    RefundRequest,
    SupportTicket,
)
from scripts.seed import seed_into

_ALLOWED_ENVIRONMENTS = {"demo", "integration", "test"}

# LangGraph creates these tables when the Postgres checkpointer initializes. They
# may not exist yet during first bootstrap, so reset only tables that are present.
_CHECKPOINT_DATA_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


def _reset_checkpoint_rows(session: Session) -> None:
    for table_name in _CHECKPOINT_DATA_TABLES:
        exists = session.execute(
            text("select to_regclass(:table_name)"),
            {"table_name": f"public.{table_name}"},
        ).scalar_one_or_none()
        if exists is not None:
            session.execute(text(f'DELETE FROM "{table_name}"'))


def reset() -> None:
    settings = get_settings()
    if settings.app_env.casefold() not in _ALLOWED_ENVIRONMENTS:
        raise RuntimeError(
            "QA fixture reset is restricted to demo, integration, or test environments."
        )

    with SessionLocal.begin() as session:
        _reset_checkpoint_rows(session)
        for model in (
            AgentRunProjectionRecord,
            PolicyAuditRecord,
            BusinessActionReceipt,
            Escalation,
            ConversationMessage,
            ConversationOwnershipEvent,
            Conversation,
            RefundRequest,
            SupportTicket,
            Order,
            MemoryRecord,
            Customer,
        ):
            session.execute(delete(model))
        seed_into(session)


if __name__ == "__main__":
    reset()
    print("Deterministic QA fixtures reset.")
