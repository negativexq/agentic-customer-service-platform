"""Follow-ups found in long live conversations: none may loop, fail, or start the wrong write."""

import re

from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeSemanticDecisionV3Provider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, SemanticDecisionV3
from app.models import Order, RefundRequest
from app.models.entities import OrderStatus, RefundStatus


class StatusReader:
    """The model reading a status question: one request about something that exists."""

    def read(self, message: str) -> dict[str, object]:
        return {"requests": [{"text": message, "status_question": True}]}


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
    runtime = AgentRuntime(provider=provider, reader=StatusReader())

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


def test_cancelling_a_cancelled_order_says_so_instead_of_asking(db_session: Session) -> None:
    db_session.add(Order(id=4204, customer_id=1, status=OrderStatus.CANCELLED, total_amount="3.00"))
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.ORDER_CANCEL,
                request_type=AgentRequestType.WRITE_ACTION,
                target={"type": "explicit_order", "order_id": 4204},
            )
        ]
    )
    answer = _say(AgentRuntime(provider=provider), db_session, "cancel-twice", "Cancel 4204")

    assert answer.pending_action is None
    assert answer.message == "Order #4204 is already cancelled, so there's nothing more to do."


def test_confirmation_and_refusals_name_the_order(db_session: Session) -> None:
    db_session.add(Order(id=4205, customer_id=1, status=OrderStatus.PENDING, total_amount="3.00"))
    db_session.add(Order(id=4206, customer_id=1, status=OrderStatus.SHIPPED, total_amount="3.00"))
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.ORDER_CANCEL,
                request_type=AgentRequestType.WRITE_ACTION,
                target={"type": "explicit_order", "order_id": order_id},
            )
            for order_id in (4205, 4206)
        ]
    )
    runtime = AgentRuntime(provider=provider)

    asked = _say(runtime, db_session, "name-order-a", "Cancel 4205")
    refused = _say(runtime, db_session, "name-order-b", "Cancel 4206")

    assert asked.message.startswith("I can submit your cancellation request for order #4205.")
    assert refused.message.startswith("I can't cancel order #4206 because")


def test_why_was_it_cancelled_gets_the_fact_not_the_policy(db_session: Session) -> None:
    db_session.add(Order(id=4207, customer_id=1, status=OrderStatus.CANCELLED, total_amount="3.00"))
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.CANCELLATION_EXPLANATION,
                request_type=AgentRequestType.KNOWLEDGE_AND_ACTION,
                target={"type": "explicit_order", "order_id": 4207},
                requires_retrieval=True,
            )
        ]
    )
    answer = _say(
        AgentRuntime(provider=provider), db_session, "why-cancel", "Why was 4207 cancelled?"
    )

    assert answer.message.startswith("Order #4207 is cancelled. Our records don't show why")


def test_out_of_scope_question_gets_the_scope() -> None:
    from app.agent.nodes.respond import respond

    result = respond({"intent": Intent.UNKNOWN, "request_type": AgentRequestType.READ_ACTION})
    assert result["final_response"].startswith("I can only help with your orders")


def test_refund_status_read_as_eligibility_still_reads_the_order(db_session: Session) -> None:
    db_session.add(Order(id=4208, customer_id=1, status=OrderStatus.DELIVERED, total_amount="9.00"))
    db_session.add(
        RefundRequest(customer_id=1, order_id=4208, reason="damaged", status=RefundStatus.REQUESTED)
    )
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.REFUND_ELIGIBILITY,
                request_type=AgentRequestType.KNOWLEDGE_AND_ACTION,
                target={"type": "explicit_order", "order_id": 4208},
                requires_retrieval=True,
            )
        ]
    )
    answer = _say(
        AgentRuntime(provider=provider, reader=StatusReader()),
        db_session,
        "refund-status-elig",
        "4208 iadesi ne durumda?",
    )

    assert answer.intent == Intent.ORDER_LOOKUP
    assert "has been submitted and is waiting for review" in answer.message


def test_someone_elses_order_reads_like_a_missing_one(db_session: Session) -> None:
    db_session.add(Order(id=4209, customer_id=2, status=OrderStatus.SHIPPED, total_amount="3.00"))
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.ORDER_LOOKUP,
                request_type=AgentRequestType.READ_ACTION,
                target={"type": "explicit_order", "order_id": 4209},
            )
        ]
    )
    answer = _say(AgentRuntime(provider=provider), db_session, "other-order", "Where is 4209?")

    assert answer.message == "I couldn't find order #4209 on your account. Please check the number."


def test_asked_reply_language_wins_over_detection(db_session: Session) -> None:
    class AsksEnglish:
        def read(self, message: str) -> dict[str, object]:
            asked = "İngilizce" in message
            return {
                "requests": [{"text": message}],
                "reply_language": "english" if asked else None,
            }

    class Turkish:
        def translate(self, reply: str, language: str) -> str:
            return "TR " + reply

    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(intent=Intent.UNKNOWN, request_type=AgentRequestType.UNCLEAR),
            SemanticDecisionV3(intent=Intent.ORDER_LIST, request_type=AgentRequestType.READ_ACTION),
        ]
    )
    runtime = AgentRuntime(provider=provider, reader=AsksEnglish(), localizer=Turkish())

    asked = _say(
        runtime, db_session, "asked-lang", "Bundan sonra bana İngilizce cevap verir misin?"
    )
    later = _say(runtime, db_session, "asked-lang", "Siparişlerim neler?")

    assert asked.message == "Sure, I'll reply in English from now on."
    assert later.message.startswith("You have")  # not translated: English was chosen


def test_quoted_customer_text_does_not_stop_translation() -> None:
    from app.agent.localize import localize_reply

    class Upper:
        def translate(self, reply: str, language: str) -> str:
            return "ÇEVRİLDİ " + reply

    reply = 'Ticket #11 (shipping delay): open. "Kargom gecikti."'
    assert localize_reply(Upper(), "11 numaralı talebim?", reply).startswith("ÇEVRİLDİ")


def test_upset_handoff_apologises_first() -> None:
    from app.agent.nodes.respond import respond

    result = respond(
        {
            "intent": Intent.HUMAN_ESCALATION,
            "request_type": AgentRequestType.ESCALATION,
            "selected_tool": "escalate_to_human",
            "tool_result": {},
            "customer_upset": True,
            "messages": [{"role": "user", "content": "Bu kaçıncı soruşum, siparişim gelmedi"}],
        }
    )
    assert result["final_response"].startswith("I'm sorry for the trouble.")


def test_half_turkish_reply_is_translated_whole() -> None:
    from app.agent.localize import localize_reply

    class Marked:
        def translate(self, reply: str, language: str) -> str:
            return "Üzgünüm. Sizi bir destek uzmanına bağlayacağım."

    mixed = "Bunu yaşadığınız için üzgünüm. I'll connect you with a human support specialist."
    assert localize_reply(Marked(), "Çok sinir bozucu", mixed).startswith("Üzgünüm.")


class SignalReader:
    """A model reading that marks one signal on every message."""

    def __init__(self, **signals: object) -> None:
        self.signals = signals

    def read(self, message: str) -> dict[str, object]:
        return {"requests": [{"text": message}], **self.signals}


def test_never_mind_drops_the_unfinished_request(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.REFUND_REQUEST, request_type=AgentRequestType.WRITE_ACTION
            )
        ]
    )
    runtime = AgentRuntime(provider=provider)
    asked = _say(runtime, db_session, "withdraw", "I want a refund")
    runtime.reader = SignalReader(withdraws=True)
    dropped = _say(runtime, db_session, "withdraw", "Vazgeçtim, gerek yok")

    assert asked.message == "Could you provide your order number?"
    assert dropped.message.startswith("No problem, I've set that aside.")
    assert len(provider.calls) == 1


def test_bare_acknowledgement_gets_a_short_reply(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [SemanticDecisionV3(intent=Intent.UNKNOWN, request_type=AgentRequestType.UNCLEAR)]
    )
    runtime = AgentRuntime(provider=provider, reader=SignalReader(acknowledgement=True))
    answer = _say(runtime, db_session, "ack", "👍")
    assert answer.message == "Great! Is there anything else I can help with?"


def test_shared_card_number_gets_a_security_note() -> None:
    from app.agent.nodes.respond import _shares_card_number

    assert _shares_card_number("Kartım 4111 1111 1111 1111, iadeyi buna yapın")
    assert not _shares_card_number("Order 1234567890123 please")  # fails the Luhn check
    assert not _shares_card_number("Sipariş 2 nerede?")


def test_second_listed_ticket_and_last_ticket_number_resolve(db_session: Session) -> None:
    from app.models import SupportTicket

    for ticket_id in (4301, 4302):
        db_session.add(
            SupportTicket(
                id=ticket_id, customer_id=1, category="billing", description="x", status="open"
            )
        )
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.TICKET_LIST, request_type=AgentRequestType.READ_ACTION
            ),
            SemanticDecisionV3(
                intent=Intent.TICKET_LOOKUP,
                request_type=AgentRequestType.READ_ACTION,
                clarification_required=True,
            ),
            SemanticDecisionV3(
                intent=Intent.TICKET_LOOKUP,
                request_type=AgentRequestType.READ_ACTION,
                clarification_required=True,
            ),
        ]
    )
    runtime = AgentRuntime(provider=provider)

    listed = _say(runtime, db_session, "ordinal-ticket", "Show my tickets")
    second = _say(runtime, db_session, "ordinal-ticket", "Show the second one")
    again = _say(runtime, db_session, "ordinal-ticket", "What was that ticket's number?")

    rows = re.findall(r"(?:: |; )#(\d+) \(", listed.message)
    assert len(rows) >= 2
    assert second.message.startswith(f"Ticket #{rows[1]} ")
    assert again.message.startswith(f"Ticket #{rows[1]} ")


def test_reader_alone_cannot_turn_a_business_question_into_a_memory_summary(
    db_session: Session,
) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [SemanticDecisionV3(intent=Intent.TICKET_LIST, request_type=AgentRequestType.READ_ACTION)]
    )
    runtime = AgentRuntime(provider=provider, reader=SignalReader(asks_memory_summary=True))
    answer = _say(runtime, db_session, "no-memory-leak", "Destek taleplerim neler?")

    assert answer.intent == Intent.TICKET_LIST
    assert "remember" not in answer.message.lower()
