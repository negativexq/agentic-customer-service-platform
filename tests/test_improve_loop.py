from collections.abc import Generator, Sequence

import pytest
from fastapi.testclient import TestClient

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, StructuredDecision
from app.agent_config.service import invalidate_cache
from app.api.routes.agent import get_agent_runtime
from app.core.config import get_settings
from app.main import app

CUSTOMER_ONE = {"Authorization": "Bearer test-customer-one-token"}


def _orders() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.ORDER_LIST,
        request_type=AgentRequestType.READ_ACTION,
        tool_name="get_customer_orders",
        arguments={"customer_id": 1},
        reason="improve test",
    )


def _unknown() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.UNKNOWN, request_type=AgentRequestType.UNCLEAR, reason="improve test"
    )


def _escalation() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.HUMAN_ESCALATION,
        request_type=AgentRequestType.ESCALATION,
        tool_name="escalate_to_human",
        arguments={
            "customer_id": 1,
            "ticket_id": 1,
            "reason": "Chargeback dispute",
            "priority": "high",
            "summary": "Customer disputes a charge",
        },
        reason="improve test",
    )


@pytest.fixture(autouse=True)
def _fresh_config() -> Generator[None, None, None]:
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def use_decisions() -> Generator[object, None, None]:
    def install(decisions: Sequence[StructuredDecision]) -> None:
        runtime = AgentRuntime(provider=FakeDecisionProvider(list(decisions)))
        app.dependency_overrides[get_agent_runtime] = lambda: runtime

    yield install
    app.dependency_overrides.pop(get_agent_runtime, None)


def _chat(client: TestClient, conversation_id: str, message: str) -> dict:
    response = client.post(
        "/agent/chat",
        json={"conversation_id": conversation_id, "customer_id": 1, "message": message},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_performance_counts_ai_resolution_handoffs_and_csat(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_orders(), _escalation()])
    _chat(client, "perf-ai", "Show my orders")
    _chat(client, "perf-human", "I want to dispute a chargeback")
    client.post("/conversations/perf-ai/feedback", json={"rating": 5}, headers=CUSTOMER_ONE)
    client.post("/conversations/perf-human/feedback", json={"rating": 2}, headers=CUSTOMER_ONE)

    body = client.get("/ui/performance", params={"days": 7}).json()
    summary = body["summary"]
    assert summary["conversations"] == 2
    assert summary["ai_resolved"] == 1
    assert summary["handed_off"] == 1
    assert summary["ai_resolution_rate"] == 0.5
    assert summary["csat_average"] == 3.5
    assert summary["csat_ai_average"] == 5.0
    assert len(body["daily"]) == 8
    assert sum(point["conversations"] for point in body["daily"]) == 2
    topics = {row["intent"]: row for row in body["topics"]}
    assert topics["human_escalation"]["handoffs"] == 1
    assert topics["order_list"]["handoff_rate"] == 0.0


def test_gaps_surface_unknown_requests_handoff_reasons_and_disabled_actions(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_unknown(), _escalation(), _orders()])
    _chat(client, "gap-unknown", "Can you change my billing currency to yen?")
    _chat(client, "gap-handoff", "My bank shows a chargeback")
    client.put(
        "/ui/agent-config/draft", json={"config": {"actions": {"get_customer_orders": "disabled"}}}
    )
    client.post("/ui/agent-config/publish", json={})
    invalidate_cache()
    _chat(client, "gap-disabled", "List my orders")

    found = {gap["key"]: gap for gap in client.get("/ui/gaps").json()}
    outside = found["action:outside"]
    assert outside["fix"] == "procedures"
    assert outside["examples"][0]["question"].startswith("Can you change my billing")
    handoff = found["handoff:chargeback dispute"]
    assert handoff["count"] == 1
    assert "chargeback" in handoff["examples"][0]["question"]
    assert found["action:disabled:get_customer_orders"]["fix"] == "actions"


def test_quality_monitors_flag_conversations_and_reviews_stick(
    client: TestClient, use_decisions
) -> None:
    use_decisions([_unknown(), _unknown(), _orders()])
    _chat(client, "qa-angry", "This is useless, I want a real person")
    client.post("/conversations/qa-angry/feedback", json={"rating": 1}, headers=CUSTOMER_ONE)
    _chat(client, "qa-coupon", "Do you have a coupon code?")

    created = client.post(
        "/ui/quality/monitors",
        json={"name": "Coupon questions", "phrases": ["coupon", " "], "sender_type": "customer"},
    )
    assert created.status_code == 201
    key = created.json()["key"]

    view = client.get("/ui/quality").json()
    flags = {(flag["monitor_key"], flag["conversation_id"]): flag for flag in view["flags"]}
    assert ("frustration", "qa-angry") in flags
    assert ("low_csat", "qa-angry") in flags
    coupon = flags[(key, "qa-coupon")]
    assert "coupon" in coupon["excerpt"].lower()
    monitors = {monitor["key"]: monitor for monitor in view["monitors"]}
    assert monitors[key]["phrases"] == ["coupon"]
    assert monitors["frustration"]["open_flags"] >= 1

    reviewed = client.post(
        "/ui/quality/reviews",
        json={
            "monitor_key": "frustration",
            "conversation_pk": flags[("frustration", "qa-angry")]["conversation_pk"],
            "status": "coached",
            "note": "Added guidance",
        },
    )
    assert reviewed.status_code == 204
    after = {
        (flag["monitor_key"], flag["conversation_id"]): flag
        for flag in client.get("/ui/quality").json()["flags"]
    }
    assert after[("frustration", "qa-angry")]["status"] == "coached"
    assert after[("frustration", "qa-angry")]["note"] == "Added guidance"

    monitor_id = int(key.split(":")[1])
    assert client.delete(f"/ui/quality/monitors/{monitor_id}").status_code == 204
    assert client.delete(f"/ui/quality/monitors/{monitor_id}").status_code == 404


def test_error_log_groups_failed_runs(client: TestClient, use_decisions) -> None:
    use_decisions([_orders()])
    _chat(client, "err-ok", "Orders please")
    body = client.get("/ui/errors").json()
    assert body["total_runs"] >= 1
    assert all(error["category"] for error in body["errors"])
    assert len(body["daily"]) == 31


def test_simulation_runs_scenarios_in_isolation(client: TestClient, monkeypatch) -> None:
    scenarios = client.get("/ui/simulations/scenarios").json()
    assert len(scenarios) >= 100
    chosen = [item["id"] for item in scenarios if item["category"] == "prompt_injection"][:2]

    run = client.post("/ui/simulations", json={"scenario_ids": chosen, "label": "Safety check"})
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["scenario_count"] == 2
    assert body["against"] == "published"
    assert body["passed_count"] == sum(item["passed"] for item in body["results"])
    assert body["results"][0]["transcript"][0]["user"]
    assert body["results"][0]["transcript"][0]["assistant"]

    listed = client.get("/ui/simulations").json()
    assert listed[0]["id"] == body["id"]
    assert listed[0]["results"] is None
    assert client.get(f"/ui/simulations/{body['id']}").json()["results"]

    assert (
        client.post(
            "/ui/simulations", json={"scenario_ids": chosen, "against": "draft"}
        ).status_code
        == 409
    )
    assert client.post("/ui/simulations", json={"scenario_ids": ["nope"]}).status_code == 422
    monkeypatch.setattr(get_settings(), "studio_editor_role", "agent_admin")
    assert (
        client.post("/ui/quality/monitors", json={"name": "x", "phrases": ["y"]}).status_code == 403
    )
