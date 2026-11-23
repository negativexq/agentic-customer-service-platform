"""Reply blocks come from customer-scoped records, never from the model."""

from sqlalchemy.orm import Session

from app.agent.blocks import OrderBlock, OrdersBlock, QuickRepliesBlock, build_blocks
from app.agent.llm.fake import FakeSemanticDecisionV3Provider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, SemanticDecisionV3
from app.models import Order
from app.models.entities import OrderStatus
from app.policies.confirmation import parse_confirmation


def test_order_list_reply_carries_a_table_of_the_customers_orders(db_session: Session) -> None:
    runtime = AgentRuntime(
        provider=FakeSemanticDecisionV3Provider(
            [
                SemanticDecisionV3(
                    intent=Intent.ORDER_LIST, request_type=AgentRequestType.READ_ACTION
                )
            ]
        )
    )

    response = runtime.run(
        conversation_id="blocks-list", customer_id=1, message="Show my orders", session=db_session
    )

    assert response.blocks and isinstance(response.blocks[0], OrdersBlock)
    rows = response.blocks[0].orders
    assert rows and all(f"#{row.id}:" in response.message for row in rows)


def test_pending_write_offers_confirmation_buttons_the_parser_accepts(
    db_session: Session,
) -> None:
    db_session.add(Order(id=4301, customer_id=1, status=OrderStatus.PENDING, total_amount="5.00"))
    db_session.flush()
    runtime = AgentRuntime(
        provider=FakeSemanticDecisionV3Provider(
            [
                SemanticDecisionV3(
                    intent=Intent.ORDER_CANCEL,
                    request_type=AgentRequestType.WRITE_ACTION,
                    target={"type": "explicit_order", "order_id": 4301},
                )
            ]
        )
    )

    response = runtime.run(
        conversation_id="blocks-confirm",
        customer_id=1,
        message="Cancel order 4301.",
        session=db_session,
    )

    buttons = response.blocks[-1]
    assert isinstance(buttons, QuickRepliesBlock) and buttons.purpose == "confirmation"
    assert [parse_confirmation(item.reply) for item in buttons.replies] == ["confirmed", "rejected"]
    for language in ("Turkish", "tr", None):
        replies = build_blocks(
            db_session,
            tenant_id="default",
            customer_id=1,
            tool_name=None,
            tool_executed=False,
            message="",
            language=language,
            awaiting_confirmation=True,
        )[0]
        assert isinstance(replies, QuickRepliesBlock)
        assert [parse_confirmation(item.reply) for item in replies.replies] == [
            "confirmed",
            "rejected",
        ]


def test_card_never_shows_another_customers_order(db_session: Session) -> None:
    db_session.add(Order(id=4302, customer_id=2, status=OrderStatus.PENDING, total_amount="5.00"))
    db_session.flush()

    foreign = build_blocks(
        db_session,
        tenant_id="default",
        customer_id=1,
        tool_name="get_order",
        tool_executed=True,
        message="Order #4302: pending",
    )
    own = build_blocks(
        db_session,
        tenant_id="default",
        customer_id=2,
        tool_name="get_order",
        tool_executed=True,
        message="Order #4302: pending",
    )

    assert foreign == []
    assert isinstance(own[0], OrderBlock) and own[0].order.id == 4302
