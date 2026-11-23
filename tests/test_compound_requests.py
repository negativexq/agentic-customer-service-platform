"""A message asking for several things gets every part answered, in order, never invented."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.blocks import OrdersBlock
from app.agent.compound import split_request
from app.agent.llm.fake import FakeSemanticDecisionV3Provider
from app.agent.runtime import AgentRuntime, _legacy_execution_context
from app.agent.schemas import AgentRequestType, Intent, SemanticDecisionV3
from app.models import Conversation, Order
from app.models.entities import OrderStatus
from app.services.conversation_messages import list_messages, record_chat_turn


class FakeSplitter:
    def __init__(self, parts: list[str] | Exception) -> None:
        self.parts = parts
        self.calls: list[str] = []

    def split(self, message: str) -> list[str]:
        self.calls.append(message)
        if isinstance(self.parts, Exception):
            raise self.parts
        return list(self.parts)


def test_split_keeps_the_message_whole_unless_the_parts_are_faithful() -> None:
    message = "Refund order 6 and also open a ticket about it"
    assert split_request(None, message) == [message]

    single = FakeSplitter(["unused", "unused"])
    assert split_request(single, "Where is order 2?") == ["Where is order 2?"]
    assert single.calls == []  # no joining word, no model call

    assert split_request(FakeSplitter(["Refund order 6", "open a ticket about it"]), message) == [
        "Refund order 6",
        "open a ticket about it",
    ]
    # A part may not carry a number the customer never wrote.
    assert split_request(FakeSplitter(["Refund order 6", "open ticket 9"]), message) == [message]
    assert split_request(FakeSplitter(["a", "b", "c", "d"]), message) == [message]
    assert split_request(FakeSplitter(TimeoutError()), message) == [message]


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
    runtime = AgentRuntime(provider=provider, splitter=splitter)

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
    runtime = AgentRuntime(provider=provider, splitter=splitter)

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

    assert db_session.get(Order, 4401).status == OrderStatus.CANCELLED
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
        provider=provider, splitter=FakeSplitter(["Show my orders", "show my tickets"])
    )
    context = _legacy_execution_context("compound-record", 1)
    message = "Show my orders and also my tickets"

    response = runtime.run(context=context, message=message, session=db_session)
    record_chat_turn(db_session, context, message, response)

    conversation = db_session.scalar(
        select(Conversation).where(Conversation.conversation_id == "compound-record")
    )
    recorded = list_messages(db_session, conversation)
    assert [item.content for item in recorded] == [
        message,
        response.earlier_replies[0].message,
        response.message.removeprefix(f"{response.earlier_replies[0].message}\n\n"),
    ]
    assert recorded[1].agent_run_id == response.earlier_replies[0].agent_run_id
    assert recorded[2].agent_run_id == response.agent_run_id
