from collections.abc import Callable, Generator, Sequence
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, StructuredDecision
from app.api.routes.agent import get_agent_runtime
from app.core.config import get_settings
from app.core.time import utc_now_naive
from app.main import app
from app.models import Conversation, Escalation
from app.services.workspace import sla_statuses, sla_targets

CUSTOMER_ONE = {"Authorization": "Bearer test-customer-one-token"}


UseDecisions = Callable[[Sequence[StructuredDecision]], None]


def _escalation(priority: str = "high") -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.HUMAN_ESCALATION,
        request_type=AgentRequestType.ESCALATION,
        tool_name="escalate_to_human",
        arguments={
            "customer_id": 1,
            "ticket_id": 1,
            "reason": "customer requested",
            "priority": priority,
            "summary": "Customer asked for a person",
        },
        reason="workspace test",
    )


@pytest.fixture
def use_decisions() -> Generator[object, None, None]:
    def install(decisions: Sequence[StructuredDecision]) -> None:
        runtime = AgentRuntime(provider=FakeDecisionProvider(list(decisions)))
        app.dependency_overrides[get_agent_runtime] = lambda: runtime

    yield install
    app.dependency_overrides.pop(get_agent_runtime, None)


def _escalated(
    client: TestClient, use_decisions: UseDecisions, conversation_id: str
) -> dict[str, Any]:
    use_decisions([_escalation()])
    response = client.post(
        "/agent/chat",
        json={"conversation_id": conversation_id, "customer_id": 1, "message": "A person please"},
    )
    assert response.status_code == 200, response.text
    entries = client.get("/operator/conversations").json()
    entry: dict[str, Any] = next(
        item for item in entries if item["conversation_id"] == conversation_id
    )
    return entry


def test_internal_notes_stay_with_operators(
    client: TestClient, use_decisions: UseDecisions
) -> None:
    entry = _escalated(client, use_decisions, "ws-notes")
    url = f"/operator/conversations/{entry['id']}/notes"

    created = client.post(url, json={"content": "Card 4111 1111 1111 1111 was disputed"})
    assert created.status_code == 201
    note = created.json()
    assert note["written_by_me"] is True
    assert note["redacted"] is True
    assert "4111 1111 1111 1111" not in note["content"]
    assert [item["id"] for item in client.get(url).json()] == [note["id"]]

    transcript = client.get("/conversations/ws-notes/messages", headers=CUSTOMER_ONE)
    assert transcript.status_code == 200
    assert "disputed" not in transcript.text
    detail = client.get(f"/operator/conversations/{entry['id']}").json()
    assert all("disputed" not in message["content"] for message in detail["messages"])

    assert client.delete(f"{url}/{note['id']}").status_code == 204
    assert client.get(url).json() == []
    assert client.delete(f"{url}/{note['id']}").status_code == 404


def test_customer_context_lists_orders_tickets_and_history(
    client: TestClient, use_decisions: UseDecisions
) -> None:
    first = _escalated(client, use_decisions, "ws-context-1")
    second = _escalated(client, use_decisions, "ws-context-2")
    context = client.get(f"/operator/conversations/{second['id']}/context")
    assert context.status_code == 200
    body = context.json()
    assert body["customer"]["id"] == 1
    assert body["orders"] and all("status" in order for order in body["orders"])
    assert body["conversation_count"] == 2
    assert [item["id"] for item in body["other_conversations"]] == [first["id"]]
    assert client.get("/operator/conversations/999999/context").status_code == 404


def test_saved_replies_crud_and_usage(client: TestClient) -> None:
    created = client.post(
        "/operator/saved-replies",
        json={"title": "Refund timing", "content": "Hi {first_name}, refunds take 5 days."},
    )
    assert created.status_code == 201
    reply = created.json()
    client.post(f"/operator/saved-replies/{reply['id']}/used")
    client.post(f"/operator/saved-replies/{reply['id']}/used")
    updated = client.put(
        f"/operator/saved-replies/{reply['id']}",
        json={"title": "Refund timing", "content": "Refunds take 5 business days."},
    ).json()
    assert updated["use_count"] == 2
    assert updated["content"] == "Refunds take 5 business days."
    assert len(client.get("/operator/saved-replies").json()) == 1
    assert client.delete(f"/operator/saved-replies/{reply['id']}").status_code == 204
    assert client.get("/operator/saved-replies").json() == []


def test_sla_targets_and_waiting_state(
    client: TestClient,
    db_session: Session,
    use_decisions: UseDecisions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = {
        item["priority"]: item["first_response_minutes"]
        for item in client.get("/operator/sla-targets").json()
    }
    assert targets == {"urgent": 15, "high": 60, "medium": 240, "low": 1440}

    entry = _escalated(client, use_decisions, "ws-sla")
    assert entry["sla"]["priority"] == "high"
    assert entry["sla"]["target_minutes"] == 60
    assert entry["sla"]["state"] == "ok"

    saved = client.put(
        "/operator/sla-targets",
        json={"targets": [{"priority": "high", "first_response_minutes": 5}]},
    )
    assert saved.status_code == 200
    conversation = db_session.get(Conversation, entry["id"])
    assert conversation is not None
    escalation = db_session.scalar(
        select(Escalation).where(Escalation.conversation_pk == conversation.id)
    )
    assert escalation is not None
    targets_now = sla_targets(db_session, "default")
    later = utc_now_naive() + timedelta(minutes=10)
    breached = sla_statuses(
        db_session, [conversation], {conversation.id: escalation}, targets_now, now=later
    )
    assert breached[conversation.id].state == "breached"
    at_risk = sla_statuses(
        db_session,
        [conversation],
        {conversation.id: escalation},
        targets_now,
        now=escalation.created_at + timedelta(minutes=4, seconds=30),
    )
    assert at_risk[conversation.id].state == "at_risk"

    # Once an operator answers, the customer is no longer waiting.
    claimed = client.post(
        f"/operator/conversations/{entry['id']}/claim",
        json={"expected_version": entry["ownership_version"]},
    ).json()
    client.post(
        f"/operator/conversations/{entry['id']}/messages",
        json={
            "content": "Hi, I am here.",
            "expected_version": claimed["ownership_version"],
            "client_message_id": "ws-sla-reply-1",
        },
    )
    assert client.get(f"/operator/conversations/{entry['id']}").json()["sla"] is None

    invalid = client.put(
        "/operator/sla-targets",
        json={"targets": [{"priority": "high", "first_response_minutes": 0}]},
    )
    assert invalid.status_code == 422
    monkeypatch.setattr(get_settings(), "studio_editor_role", "agent_admin")
    forbidden = client.put(
        "/operator/sla-targets",
        json={"targets": [{"priority": "high", "first_response_minutes": 30}]},
    )
    assert forbidden.status_code == 403


def test_customer_rates_conversation_and_operator_sees_it(
    client: TestClient, use_decisions: UseDecisions
) -> None:
    entry = _escalated(client, use_decisions, "ws-csat")
    rated = client.post(
        "/conversations/ws-csat/feedback",
        json={"rating": 4, "comment": "Quick help"},
        headers=CUSTOMER_ONE,
    )
    assert rated.status_code == 200
    assert rated.json()["resolved_by"] == "human"
    again = client.post("/conversations/ws-csat/feedback", json={"rating": 5}, headers=CUSTOMER_ONE)
    assert again.json()["rating"] == 5
    assert client.get(f"/operator/conversations/{entry['id']}").json()["csat_rating"] == 5
    context = client.get(f"/operator/conversations/{entry['id']}/context").json()
    assert context["feedback"]["rating"] == 5
    assert context["average_rating"] == 5.0
    assert (
        client.post(
            "/conversations/ws-csat/feedback", json={"rating": 6}, headers=CUSTOMER_ONE
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/conversations/unknown/feedback", json={"rating": 3}, headers=CUSTOMER_ONE
        ).status_code
        == 404
    )
