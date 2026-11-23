from collections.abc import Generator, Sequence

import pytest
from fastapi.testclient import TestClient

from app.agent.handoff import HANDOFF_OFFER_MESSAGE
from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, StructuredDecision
from app.agent_config.service import invalidate_cache
from app.api.routes.agent import get_agent_runtime
from app.main import app

CONNECTED = "I'll connect you with a human support specialist."


def _handoff(priority: str = "normal") -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.HUMAN_ESCALATION,
        request_type=AgentRequestType.ESCALATION,
        tool_name="escalate_to_human",
        arguments={
            "customer_id": 1,
            "ticket_id": None,
            "reason": "Customer requested human assistance",
            "priority": priority,
            "summary": "Customer asked to speak with a person",
        },
        reason="handoff test",
    )


@pytest.fixture(autouse=True)
def _fresh_config() -> Generator[None, None, None]:
    invalidate_cache()
    yield
    invalidate_cache()
    app.dependency_overrides.pop(get_agent_runtime, None)


def _use(decisions: Sequence[StructuredDecision]) -> None:
    runtime = AgentRuntime(provider=FakeDecisionProvider(list(decisions)))
    app.dependency_overrides[get_agent_runtime] = lambda: runtime


def _chat(client: TestClient, conversation_id: str, message: str) -> dict:
    response = client.post(
        "/agent/chat",
        json={"conversation_id": conversation_id, "customer_id": 1, "message": message},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_plain_request_gets_one_offer_then_a_person(client: TestClient) -> None:
    _use([_handoff(), _handoff()])

    offered = _chat(client, "handoff-offer", "Can I talk to a person?")
    assert offered["message"] == HANDOFF_OFFER_MESSAGE
    assert offered["tool_call"] is None
    assert offered["conversation_owner"] == "ai"

    connected = _chat(client, "handoff-offer", "No, just connect me please.")
    assert connected["message"] == CONNECTED
    assert connected["tool_call"]["status"] == "executed"
    assert connected["conversation_owner"] == "queued"


@pytest.mark.parametrize(
    ("priority", "message"),
    [
        ("high", "Someone charged my card and I never authorized it."),
        ("urgent", "I think my account was hacked, get me a person."),
        ("normal", "This is useless, I want a real person."),
    ],
)
def test_sensitive_or_upset_requests_connect_immediately(
    client: TestClient, priority: str, message: str
) -> None:
    _use([_handoff(priority)])

    response = _chat(client, f"handoff-now-{priority}", message)

    assert response["message"].endswith(CONNECTED)
    assert response["tool_call"]["status"] == "executed"


def test_operators_can_choose_immediate_handoff(client: TestClient) -> None:
    client.put("/ui/agent-config/draft", json={"config": {"handoff": "immediate"}})
    client.post("/ui/agent-config/publish", json={})
    invalidate_cache()
    _use([_handoff()])

    response = _chat(client, "handoff-config", "Can I talk to a person?")

    assert response["message"].endswith(CONNECTED)


class _Reader:
    """Reads every message as one request, with the model's explains_issue signal fixed."""

    def __init__(self, explains_issue: bool) -> None:
        self.explains_issue = explains_issue

    def read(self, message: str) -> dict[str, object]:
        return {"requests": [{"text": message}], "explains_issue": self.explains_issue}


def _use_with_reader(decisions: Sequence[StructuredDecision], explains_issue: bool) -> None:
    runtime = AgentRuntime(
        provider=FakeDecisionProvider(list(decisions)), reader=_Reader(explains_issue)
    )
    app.dependency_overrides[get_agent_runtime] = lambda: runtime


def test_request_that_says_what_it_is_about_connects_immediately(client: TestClient) -> None:
    _use_with_reader([_handoff("medium")], explains_issue=True)

    connected = _chat(
        client, "handoff-explained", "Can a person help me with a bulk order for my company?"
    )

    assert connected["message"] == CONNECTED
    assert connected["tool_call"]["status"] == "executed"
    assert connected["conversation_owner"] == "queued"


def test_bare_request_read_by_the_model_still_gets_one_offer(client: TestClient) -> None:
    _use_with_reader([_handoff("medium")], explains_issue=False)

    offered = _chat(client, "handoff-bare", "Can I talk to someone?")

    assert offered["message"] == HANDOFF_OFFER_MESSAGE
    assert offered["conversation_owner"] == "ai"
