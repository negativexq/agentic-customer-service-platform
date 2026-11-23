"""Follow-ups found in long live conversations: none may loop, fail, or start the wrong write."""

from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeSemanticDecisionV3Provider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, SemanticDecisionV3
from app.models import Order, RefundRequest
from app.models.entities import OrderStatus, RefundStatus


def _say(runtime: AgentRuntime, session: Session, conversation: str, message: str):
    return runtime.run(
        conversation_id=conversation, customer_id=1, message=message, session=session
    )


def test_upset_reply_to_a_ticket_question_reaches_the_model(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.TICKET_LOOKUP, request_type=AgentRequestType.READ_ACTION
            ),
            SemanticDecisionV3(
                intent=Intent.HUMAN_ESCALATION, request_type=AgentRequestType.ESCALATION
            ),
        ]
    )
    runtime = AgentRuntime(provider=provider)

    asked = _say(runtime, db_session, "ticket-loop", "What's the status of my ticket?")
    upset = _say(runtime, db_session, "ticket-loop", "This is ridiculous, get me a person now.")

    assert asked.message.startswith("Which ticket do you mean?")
    assert len(provider.calls) == 2
    assert upset.intent == Intent.HUMAN_ESCALATION


def test_read_while_a_cancellation_waits_answers_and_keeps_it(db_session: Session) -> None:
    db_session.add(Order(id=4201, customer_id=1, status=OrderStatus.PENDING, total_amount="19.00"))
    db_session.flush()
    target = {"type": "explicit_order", "order_id": 4201}
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.ORDER_CANCEL,
                request_type=AgentRequestType.WRITE_ACTION,
                target=target,
            ),
            SemanticDecisionV3(
                intent=Intent.ORDER_LOOKUP, request_type=AgentRequestType.READ_ACTION, target=target
            ),
        ]
    )
    runtime = AgentRuntime(provider=provider)

    pending = _say(runtime, db_session, "read-while-pending", "Cancel order 4201 please.")
    answer = _say(runtime, db_session, "read-while-pending", "Is order 4201 cancelled now?")

    assert pending.pending_action is not None
    assert answer.message.startswith("Order #4201: pending")
    assert "still saved and waiting for your confirmation" in answer.message
    assert db_session.get(Order, 4201).status == OrderStatus.PENDING


def test_refund_status_question_reads_the_order_instead_of_a_new_refund(
    db_session: Session,
) -> None:
    db_session.add(Order(id=4202, customer_id=1, status=OrderStatus.DELIVERED, total_amount="9.00"))
    db_session.add(
        RefundRequest(customer_id=1, order_id=4202, reason="damaged", status=RefundStatus.REQUESTED)
    )
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.REFUND_REQUEST,
                request_type=AgentRequestType.WRITE_ACTION,
                target={"type": "explicit_order", "order_id": 4202},
            )
        ]
    )
    runtime = AgentRuntime(provider=provider)

    answer = _say(
        runtime, db_session, "refund-status", "What's the status of my refund for order 4202?"
    )

    assert answer.intent == Intent.ORDER_LOOKUP
    assert answer.pending_action is None
    assert "has been submitted and is waiting for review" in answer.message


def test_thanks_is_not_taken_as_a_refund_reason(db_session: Session) -> None:
    db_session.add(Order(id=4203, customer_id=1, status=OrderStatus.DELIVERED, total_amount="9.00"))
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.REFUND_REQUEST,
                request_type=AgentRequestType.WRITE_ACTION,
                target={"type": "explicit_order", "order_id": 4203},
            ),
            SemanticDecisionV3(intent=Intent.UNKNOWN, request_type=AgentRequestType.UNCLEAR),
        ]
    )
    runtime = AgentRuntime(provider=provider)

    asked = _say(runtime, db_session, "refund-thanks", "I want a refund for order 4203.")
    closing = _say(runtime, db_session, "refund-thanks", "Thanks, that's all.")

    assert asked.message == "Could you briefly tell me why you are requesting this refund?"
    assert closing.pending_action is None
    assert closing.message == "You're welcome! I'm here if you need anything else."


def test_identity_closing_and_recap_get_plain_answers() -> None:
    from app.agent.nodes.respond import _CLOSING, _IDENTITY, _RECAP, _asks_arrival

    assert _IDENTITY.search("Sen bot musun?") and _IDENTITY.search("Who are you?")
    assert _CLOSING.match("Tamam teşekkürler") and _CLOSING.match("ok bye")
    assert _RECAP.search("What did we talk about so far?") and _RECAP.search("Ne konuştuk?")
    assert _asks_arrival("Ne zaman gelir?") and _asks_arrival("When will it arrive?")
    assert not _asks_arrival("Where is order 2?")


def test_conversation_language_survives_a_bare_number() -> None:
    from app.agent.localize import conversation_language

    assert conversation_language(["Siparişim nerede?", "2"]) == "Turkish"
    assert conversation_language(["Peki onun iadesi ne durumda?"]) == "Turkish"
    assert conversation_language(["Siparişim nerede?", "Where is my order?"]) is None
