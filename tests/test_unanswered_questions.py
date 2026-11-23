from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Conversation, ConversationOwner
from app.models.entities import ConversationMessage
from app.services.insights import unanswered_questions
from app.ui.schemas import AgentRunView

START = datetime(2026, 11, 20, 9, 0, 0)


def _run(run_id: str, intent: str, minutes: int, *, grounded: bool = False) -> AgentRunView:
    return AgentRunView.model_validate(
        {
            "run_id": run_id,
            "request_id": run_id,
            "conversation_id": "conv-k",
            "customer_id": 1,
            "actor_id": "customer-1",
            "actor_type": "customer",
            "intent": intent,
            "request_type": "knowledge_only",
            "status": "completed",
            "started_at": START + timedelta(minutes=minutes),
            "duration_ms": 10,
            "memory": {"item_count": 0, "keys": [], "types": []},
            "rag_documents": [
                {
                    "citation_id": "shipping-policy#1",
                    "title": "Shipping Policy",
                    "section": "Delivery times",
                    "source": "shipping-policy",
                    "score": 0.8,
                }
            ]
            if grounded
            else [],
            "answer_grounding": {"status": "grounded" if grounded else "ungrounded"},
        }
    )


def test_questions_without_a_grounded_answer_are_listed_once_with_their_article(
    db_session: Session,
) -> None:
    conversation = Conversation(
        tenant_id="default",
        customer_id=1,
        conversation_id="conv-k",
        owner=ConversationOwner.AI,
        ownership_version=0,
        created_at=START,
        updated_at=START,
    )
    db_session.add(conversation)
    db_session.flush()
    asked = [
        ("r1", "Do you ship internationally?"),
        ("r2", "do you ship  internationally"),
        ("r3", "Where can I find warranty information?"),
        ("r4", "How long does shipping take?"),
    ]
    for sequence, (run_id, text) in enumerate(asked, 1):
        db_session.add(
            ConversationMessage(
                tenant_id="default",
                conversation_pk=conversation.id,
                customer_id=1,
                sequence=sequence,
                sender_type="customer",
                content=text,
                agent_run_id=run_id,
                redaction_state="none",
                created_at=START + timedelta(minutes=sequence),
                expires_at=START + timedelta(days=30),
            )
        )
    db_session.commit()
    runs = [
        _run("r1", "shipping_policy", 1),
        _run("r2", "shipping_policy", 2),
        _run("r3", "support_faq", 3),
        _run("r4", "shipping_policy", 4, grounded=True),
    ]

    found = unanswered_questions(db_session, runs)

    assert [item.question for item in found] == [
        "do you ship  internationally",
        "Where can I find warranty information?",
    ]
    assert found[0].asked == 2
    assert found[0].article_id == "shipping-policy"
    assert found[1].article_id == "support-faq"
    assert found[1].conversation_pk == conversation.id
