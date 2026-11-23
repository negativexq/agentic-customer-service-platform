from app.agent.nodes.retrieve_knowledge import _with_order_status


def test_order_status_leads_in_plain_words() -> None:
    assert _with_order_status({"id": 2, "status": "shipped"}, "") == "Order #2 has already shipped."
    assert (
        _with_order_status({"id": 2, "status": "shipped"}, "Cancellation ends at handoff.")
        == "Order #2 has already shipped. Cancellation ends at handoff."
    )
