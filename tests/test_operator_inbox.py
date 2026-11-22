from collections.abc import Generator, Sequence
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import HANDOFF_ACKNOWLEDGEMENT, AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, StructuredDecision
from app.api.routes.agent import get_agent_runtime
from app.auth.models import ActorType, Principal, PrincipalType
from app.auth.scope import operator_customer_filter
from app.core.config import get_settings
from app.core.time import utc_now_naive
from app.main import app
from app.memory.dlp import redact_message_text
from app.models import Conversation, ConversationMessage
from app.services.conversation_messages import (
    delete_customer_messages,
    list_messages,
    purge_expired_messages,
)

TEST_CUSTOMER_ONE_TOKEN = "test-customer-one-token"
TEST_CUSTOMER_TWO_TOKEN = "test-customer-two-token"


def _orders() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.ORDER_LIST,
        request_type=AgentRequestType.READ_ACTION,
        tool_name="get_customer_orders",
        arguments={"customer_id": 1},
        reason="inbox test",
    )


def _escalation() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.HUMAN_ESCALATION,
        request_type=AgentRequestType.ESCALATION,
        tool_name="escalate_to_human",
        arguments={
            "customer_id": 1,
            "ticket_id": 1,
            "reason": "customer requested",
            "priority": "high",
            "summary": "Customer asked for a person",
        },
        reason="inbox test",
    )


@pytest.fixture
def use_decisions() -> Generator[object, None, None]:
    def install(decisions: Sequence[StructuredDecision]) -> None:
        runtime = AgentRuntime(provider=FakeDecisionProvider(list(decisions)))
        app.dependency_overrides[get_agent_runtime] = lambda: runtime

    yield install
    app.dependency_overrides.pop(get_agent_runtime, None)


def _chat(client: TestClient, conversation_id: str, message: str) -> dict[str, object]:
    response = client.post(
        "/agent/chat",
        json={"conversation_id": conversation_id, "customer_id": 1, "message": message},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _inbox_entry(client: TestClient, conversation_id: str) -> dict[str, object]:
    entries = client.get("/operator/conversations").json()
    return next(item for item in entries if item["conversation_id"] == conversation_id)


def test_every_chat_turn_is_recorded_with_redaction(
    client: TestClient, db_session: Session, use_decisions
) -> None:
    use_decisions([_orders()])

    reply = _chat(client, "inbox-record", "Where are my orders? Mail me at ada@example.com")

    messages = db_session.scalars(
        select(ConversationMessage).order_by(ConversationMessage.sequence)
    ).all()
    assert [m.sender_type for m in messages] == ["customer", "ai"]
    assert messages[0].content == "Where are my orders? Mail me at [REDACTED_EMAIL]"
    assert messages[0].redaction_state == "redacted"
    assert messages[1].content == reply["message"]
    assert {m.agent_run_id for m in messages} == {reply["agent_run_id"]}
    assert all(m.expires_at > m.created_at for m in messages)


def test_inbox_lists_conversations_with_customer_last_message_and_escalation(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_orders(), _escalation()])
    _chat(client, "inbox-list", "Show my orders")
    _chat(client, "inbox-list", "I want to talk to a person")

    entry = _inbox_entry(client, "inbox-list")
    assert entry["customer_name"] == "Test Customer"
    assert entry["owner"] == "queued"
    assert entry["escalation"]["priority"] == "high"
    assert entry["escalation"]["summary"] == "Customer asked for a person"
    assert entry["last_message"]["sender_type"] == "ai"

    detail = client.get(f"/operator/conversations/{entry['id']}").json()
    assert [m["sender_type"] for m in detail["messages"]] == ["customer", "ai", "customer", "ai"]
    queued_only = client.get("/operator/conversations", params={"owner": "queued"}).json()
    assert [item["conversation_id"] for item in queued_only] == ["inbox-list"]


def test_operator_claims_replies_and_customer_sees_it_live(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_escalation()])
    _chat(client, "inbox-claim", "Get me a human")
    entry = _inbox_entry(client, "inbox-claim")

    claimed = client.post(
        f"/operator/conversations/{entry['id']}/claim",
        json={"expected_version": entry["ownership_version"]},
    )
    assert claimed.status_code == 200
    claimed_body = claimed.json()
    assert claimed_body["owner"] == "human"
    assert claimed_body["assigned_to_me"] is True
    assert claimed_body["messages"][-1]["sender_type"] == "system"
    assert "joined the conversation" in claimed_body["messages"][-1]["content"]

    replied = client.post(
        f"/operator/conversations/{entry['id']}/messages",
        json={
            "expected_version": claimed_body["ownership_version"],
            "content": "Hi, I am here to help.",
            "client_message_id": "reply-0001",
        },
    )
    assert replied.status_code == 200
    operator_message = replied.json()["messages"][-1]
    assert operator_message["sender_type"] == "operator"
    assert operator_message["sent_by_me"] is True
    assert operator_message["sender_name"] == "Operator"

    # The customer keeps chatting; the AI stays silent and the turn is still recorded.
    reply = _chat(client, "inbox-claim", "Thanks!")
    assert reply["message"] == HANDOFF_ACKNOWLEDGEMENT

    after = operator_message["sequence"]
    customer_view = client.get(
        "/conversations/inbox-claim/messages",
        params={"after": after - 1},
        headers={"Authorization": f"Bearer {TEST_CUSTOMER_ONE_TOKEN}"},
    )
    assert customer_view.status_code == 200
    body = customer_view.json()
    assert body["owner"] == "human"
    assert [m["sender_type"] for m in body["messages"]] == ["operator", "customer", "system"]
    assert "sender_actor_id" not in customer_view.text
    assert "operator-test" not in customer_view.text


def test_reply_is_idempotent_and_requires_current_assignee_version(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_escalation()])
    _chat(client, "inbox-idem", "Human please")
    entry = _inbox_entry(client, "inbox-idem")
    claimed = client.post(
        f"/operator/conversations/{entry['id']}/claim",
        json={"expected_version": entry["ownership_version"]},
    ).json()
    payload = {
        "expected_version": claimed["ownership_version"],
        "content": "One reply",
        "client_message_id": "reply-dup-1",
    }

    first = client.post(f"/operator/conversations/{entry['id']}/messages", json=payload).json()
    second = client.post(f"/operator/conversations/{entry['id']}/messages", json=payload).json()
    assert len(first["messages"]) == len(second["messages"])

    stale = client.post(
        f"/operator/conversations/{entry['id']}/messages",
        json={
            **payload,
            "expected_version": entry["ownership_version"],
            "client_message_id": "x" * 9,
        },
    )
    assert stale.status_code == 409
    stale_claim = client.post(
        f"/operator/conversations/{entry['id']}/claim",
        json={"expected_version": entry["ownership_version"]},
    )
    assert stale_claim.status_code == 409


def test_operator_can_take_over_a_live_ai_conversation_and_hand_it_back(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_orders(), _orders()])
    _chat(client, "inbox-takeover", "Orders?")
    entry = _inbox_entry(client, "inbox-takeover")
    assert entry["owner"] == "ai"

    taken = client.post(
        f"/operator/conversations/{entry['id']}/claim",
        json={"expected_version": entry["ownership_version"]},
    ).json()
    assert taken["owner"] == "human"
    assert taken["escalation"] is None

    released = client.post(
        f"/operator/conversations/{entry['id']}/release",
        json={"expected_version": taken["ownership_version"]},
    ).json()
    assert released["owner"] == "ai"
    assert "handed the conversation back" in released["messages"][-1]["content"]
    assert _chat(client, "inbox-takeover", "Orders again?")["message"] != HANDOFF_ACKNOWLEDGEMENT


def test_resolve_closes_and_the_next_customer_message_reopens_to_ai(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_escalation(), _orders()])
    _chat(client, "inbox-resolve", "Human")
    entry = _inbox_entry(client, "inbox-resolve")
    claimed = client.post(
        f"/operator/conversations/{entry['id']}/claim",
        json={"expected_version": entry["ownership_version"]},
    ).json()
    resolved = client.post(
        f"/operator/conversations/{entry['id']}/resolve",
        json={"expected_version": claimed["ownership_version"]},
    ).json()
    assert resolved["owner"] == "closed"
    assert resolved["escalation"]["status"] == "resolved"

    _chat(client, "inbox-resolve", "One more question")
    assert _inbox_entry(client, "inbox-resolve")["owner"] == "ai"


def test_customer_cannot_read_another_customers_transcript(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_orders()])
    _chat(client, "inbox-private", "Orders?")

    other = client.get(
        "/conversations/inbox-private/messages",
        headers={"Authorization": f"Bearer {TEST_CUSTOMER_TWO_TOKEN}"},
    )
    assert other.status_code == 404


def test_oidc_operator_scope_and_optional_tenant_wide_role(monkeypatch) -> None:
    operator = Principal(
        actor_id="oidc:abc",
        actor_type=ActorType.SUPPORT_OPERATOR,
        principal_type=PrincipalType.OIDC,
        roles=["support_operator", "support_tenant_wide"],
        tenant_id="tenant-a",
        customer_ids=[1],
    )
    assert operator_customer_filter(operator) == [1]
    monkeypatch.setattr(get_settings(), "oidc_support_tenant_wide_role", "support_tenant_wide")
    assert operator_customer_filter(operator) is None


def test_expired_and_deleted_messages_are_not_served(
    client: TestClient, db_session: Session, use_decisions
) -> None:
    use_decisions([_orders()])
    _chat(client, "inbox-ttl", "Orders?")
    conversation = db_session.scalar(select(Conversation))
    assert conversation is not None
    future = utc_now_naive() + timedelta(days=get_settings().conversation_message_ttl_days + 1)

    assert list_messages(db_session, conversation, now=future) == []
    assert purge_expired_messages(db_session, now=future) == 2
    _chat(client, "inbox-ttl", "Orders again?")
    assert delete_customer_messages(db_session, tenant_id="default", customer_id=1) == 2
    assert list_messages(db_session, conversation) == []


def test_message_redaction_keeps_ordinary_text_and_masks_values() -> None:
    assert redact_message_text("Order 1 arrived damaged") == ("Order 1 arrived damaged", False)
    text, changed = redact_message_text("card 4242 4242 4242 4242, call +90 555 123 45 67")
    assert changed is True
    assert "4242" not in text and "555" not in text
    assert "[REDACTED_CARD]" in text and "[REDACTED_PHONE]" in text


def test_projection_keeps_chunk_text_and_only_memory_references() -> None:
    from app.ui.projection import _memory_item, _preview

    assert _preview("  Damaged   items\nare refundable. ") == "Damaged items are refundable."
    assert _preview("x" * 900).endswith("…")
    item = _memory_item(
        {
            "id": 7,
            "normalized_key": "response_style",
            "memory_type": "preference",
            "content": "PRIVATE_MEMORY_TEXT",
        }
    )
    assert item is not None and item.memory_id == 7 and item.content is None
    assert "PRIVATE_MEMORY_TEXT" not in item.model_dump_json()
    assert _memory_item({"normalized_key": "k", "sensitivity_level": "restricted"}) is None


def test_run_memory_endpoint_resolves_live_content_and_hides_deleted(
    client: TestClient, db_session: Session
) -> None:
    from app.memory.models import MemoryRecord
    from app.memory.schemas import MemorySource, MemoryStatus, MemoryType
    from app.ui.projection import _memory_item
    from app.ui.repository import build_agent_run_projection_repository
    from app.ui.schemas import UIMemoryUsage

    active = MemoryRecord(
        customer_id=1,
        memory_type=MemoryType.PREFERENCE,
        content="Prefers short answers",
        normalized_key="response_style",
        source=MemorySource.USER_EXPLICIT,
        status=MemoryStatus.ACTIVE,
    )
    deleted = MemoryRecord(
        customer_id=1,
        memory_type=MemoryType.PREFERENCE,
        content="Old contact detail",
        normalized_key="contact",
        source=MemorySource.USER_EXPLICIT,
        status=MemoryStatus.DELETED,
    )
    db_session.add_all([active, deleted])
    db_session.commit()

    response = _chat_projection(client)
    repository = build_agent_run_projection_repository(get_settings(), db_session)
    run = repository.get_by_run_id(response, tenant_id="default")
    assert run is not None
    usage = UIMemoryUsage(
        item_count=2,
        read_items=[
            _memory_item({"id": active.id, "normalized_key": "response_style"}),
            _memory_item({"id": deleted.id, "normalized_key": "contact"}),
        ],
    )
    patched = run.model_copy(update={"memory": usage})
    repository_get = repository.get_by_run_id

    import app.api.routes.ui as ui_routes

    class _Repo:
        def get_by_run_id(self, run_id: str, tenant_id: str):  # type: ignore[no-untyped-def]
            return patched if run_id == response else repository_get(run_id, tenant_id=tenant_id)

    original = ui_routes.build_agent_run_projection_repository
    ui_routes.build_agent_run_projection_repository = lambda *_: _Repo()  # type: ignore[assignment]
    try:
        body = client.get(f"/ui/agent-runs/{response}/memory").json()
    finally:
        ui_routes.build_agent_run_projection_repository = original
    assert [item["content"] for item in body["read_items"]] == ["Prefers short answers", None]
    assert body["read_items"][1]["status"] == "deleted"


def _chat_projection(client: TestClient) -> str:
    runtime = AgentRuntime(provider=FakeDecisionProvider([_orders()]))
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    try:
        return str(_chat(client, "inbox-memory", "Orders?")["agent_run_id"])
    finally:
        app.dependency_overrides.pop(get_agent_runtime, None)


def test_operator_settings_are_read_only_and_secret_free(client: TestClient) -> None:
    body = client.get("/operator/settings").json()
    assert body["display_name"]
    assert body["customer_scope"] == "tenant"
    assert body["message_retention_days"] == get_settings().conversation_message_ttl_days
    assert not {"email", "subject", "credential_id"} & set(body)
