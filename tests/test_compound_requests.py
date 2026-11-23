"""A message asking for several things gets every part answered, in order, never invented."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.blocks import OrdersBlock
from app.agent.compound import read_message
from app.agent.llm.fake import FakeSemanticDecisionV3Provider
from app.agent.runtime import AgentRuntime, _legacy_execution_context
from app.agent.schemas import AgentRequestType, Intent, SemanticDecisionV3
from app.models import Conversation, Order
from app.models.entities import OrderStatus
from app.services.conversation_messages import list_messages, record_chat_turn


class FakeSplitter:
    """A message reader that only splits; status, upset and language stay unset."""

    def __init__(self, parts: list[str] | Exception) -> None:
        self.parts = parts
        self.calls: list[str] = []

    def read(self, message: str) -> dict[str, object]:
        self.calls.append(message)
        if isinstance(self.parts, Exception):
            raise self.parts
        return {"requests": [{"text": part} for part in self.parts]}


def _texts(reader: FakeSplitter | None, message: str) -> list[str]:
    return [request.text for request in read_message(reader, message).requests]


def test_split_keeps_the_message_whole_unless_the_parts_are_faithful() -> None:
    message = "Refund order 6 and also open a ticket about it"
    assert _texts(None, message) == [message]
    assert _texts(FakeSplitter(["Refund order 6", "open a ticket about it"]), message) == [
        "Refund order 6",
        "open a ticket about it",
    ]
    # A part may not carry a number the customer never wrote.
    assert _texts(FakeSplitter(["Refund order 6", "open ticket 9"]), message) == [message]
    assert _texts(FakeSplitter(["a", "b", "c", "d"]), message) == [message]
    assert _texts(FakeSplitter(TimeoutError()), message) == [message]
    # One request keeps the customer's exact words even if the model reworded it.
    assert _texts(FakeSplitter(["refund for order 6"]), message) == [message]


def test_reading_carries_the_models_signals() -> None:
    class Reader:
        def read(self, message: str) -> dict[str, object]:
            return {
                "requests": [{"text": message, "status_question": True}],
                "upset": True,
                "reply_language": "english",
            }

    reading = read_message(Reader(), "Bu kaçıncı soruşum, iadem ne oldu?")
    assert reading.upset is True and reading.reply_language == "english"
    assert reading.requests[0].status_question is True
    assert read_message(None, "x").upset is None


def test_orders_and_a_policy_question_both_get_answered(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(intent=Intent.ORDER_LIST, request_type=AgentRequestType.READ_ACTION),
            SemanticDecisionV3(
                intent=Intent.CANCELLATION_POLICY,
                request_type=AgentRequestType.KNOWLEDGE_ONLY,
                requires_retrieval=True,
                knowledge_query="cancellation policy",
            ),
        ]
    )
    splitter = FakeSplitter(["What are my orders?", "What is your cancellation policy?"])
    runtime = AgentRuntime(provider=provider, reader=splitter)

    response = runtime.run(
        conversation_id="compound-read",
        customer_id=1,
        message="What are my orders and what is your cancellation policy?",
        session=db_session,
    )

    assert len(provider.calls) == 2
    assert len(response.earlier_replies) == 1
    first = response.earlier_replies[0].message
    assert response.message.startswith(f"{first}\n\n")
    assert response.message.removeprefix(f"{first}\n\n").strip()
    assert any(isinstance(block, OrdersBlock) for block in response.blocks)
    assert response.intent == Intent.CANCELLATION_POLICY


def test_second_request_waits_for_the_first_confirmation(db_session: Session) -> None:
    db_session.add(Order(id=4401, customer_id=1, status=OrderStatus.PENDING, total_amount="7.00"))
    db_session.flush()
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.ORDER_CANCEL,
                request_type=AgentRequestType.WRITE_ACTION,
                target={"type": "explicit_order", "order_id": 4401},
            ),
            SemanticDecisionV3(intent=Intent.ORDER_LIST, request_type=AgentRequestType.READ_ACTION),
        ]
    )
    splitter = FakeSplitter(["Cancel order 4401", "show me my orders"])
    runtime = AgentRuntime(provider=provider, reader=splitter)

    asked = runtime.run(
        conversation_id="compound-write",
        customer_id=1,
        message="Cancel order 4401 and then show me my orders",
        session=db_session,
    )

    assert asked.pending_action is not None
    assert len(provider.calls) == 1  # the list waits until the cancellation is settled
    assert asked.message.endswith("Once this is done, I'll take care of your other request too.")

    done = runtime.run(
        conversation_id="compound-write", customer_id=1, message="Yes", session=db_session
    )

    order = db_session.get(Order, 4401)
    assert order is not None
    assert order.status == OrderStatus.CANCELLED
    assert len(provider.calls) == 2
    assert len(done.earlier_replies) == 1 and "4401" in done.earlier_replies[0].message
    assert any(isinstance(block, OrdersBlock) for block in done.blocks)

    again = runtime.run(
        conversation_id="compound-write", customer_id=1, message="Thanks!", session=db_session
    )
    assert not again.earlier_replies  # the queued request ran once


def test_each_reply_is_recorded_as_its_own_message(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(intent=Intent.ORDER_LIST, request_type=AgentRequestType.READ_ACTION),
            SemanticDecisionV3(
                intent=Intent.TICKET_LIST, request_type=AgentRequestType.READ_ACTION
            ),
        ]
    )
    runtime = AgentRuntime(
        provider=provider, reader=FakeSplitter(["Show my orders", "show my tickets"])
    )
    context = _legacy_execution_context("compound-record", 1)
    message = "Show my orders and also my tickets"

    response = runtime.run(context=context, message=message, session=db_session)
    record_chat_turn(db_session, context, message, response)

    conversation = db_session.scalar(
        select(Conversation).where(Conversation.conversation_id == "compound-record")
    )
    assert conversation is not None
    recorded = list_messages(db_session, conversation)
    assert [item.content for item in recorded] == [
        message,
        response.earlier_replies[0].message,
        response.message.removeprefix(f"{response.earlier_replies[0].message}\n\n"),
    ]
    assert recorded[1].agent_run_id == response.earlier_replies[0].agent_run_id
    assert recorded[2].agent_run_id == response.agent_run_id


def test_a_message_asking_for_nothing_keeps_its_signals() -> None:
    class Reader:
        def read(self, message: str) -> dict[str, object]:
            return {"requests": [], "acknowledgement": True}

    reading = read_message(Reader(), "ok")
    assert reading.acknowledgement is True
    assert [request.text for request in reading.requests] == ["ok"]


def test_model_read_list_position_resolves_against_our_own_listing() -> None:
    from app.agent.semantic_grounding import listed_reference

    listing = (
        "You have 3 support tickets: #4 (product about order #6): closed; "
        "#6 (invoice): open; #7 (billing): open."
    )
    # "İkincisinin" is not a fixed ordinal word; the model's position still resolves it.
    assert listed_reference("İkincisinin detayını göster", listing) is None
    assert listed_reference("İkincisinin detayını göster", listing, 2) == 6
    assert listed_reference("the last one", listing, -1) == 7
    assert listed_reference("the fifth one", listing, 5) is None


def test_reading_keeps_whether_the_message_explains_the_issue() -> None:
    class Reader:
        def read(self, message: str) -> dict[str, object]:
            return {"requests": [{"text": message}], "explains_issue": True}

    assert read_message(Reader(), "My parcel never arrived, get me a person").explains_issue
    assert read_message(None, "x").explains_issue is None
