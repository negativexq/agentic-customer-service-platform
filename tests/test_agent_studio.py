from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, StructuredDecision
from app.agent_config.context import guidance_prompt, use_agent_config
from app.agent_config.schemas import ActionMode, AgentConfig, GuidanceRule
from app.agent_config.service import invalidate_cache
from app.api.routes.agent import get_agent_runtime
from app.core.config import get_settings
from app.core.context import ExecutionContext
from app.main import app
from app.policies.models import PolicyOutcome
from app.policies.rules import evaluate_default_policy
from app.services.studio import knowledge


@pytest.fixture(autouse=True)
def _fresh_config_cache() -> Generator[None, None, None]:
    invalidate_cache()
    yield
    invalidate_cache()


def _orders() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.ORDER_LIST,
        request_type=AgentRequestType.READ_ACTION,
        tool_name="get_customer_orders",
        arguments={"customer_id": 1},
        reason="studio test",
    )


def _context() -> ExecutionContext:
    from app.agent.runtime import _legacy_execution_context

    return _legacy_execution_context("studio-policy", 1)


def test_config_only_narrows_authority() -> None:
    with pytest.raises(ValidationError):
        AgentConfig(actions={"escalate_to_human": ActionMode.DISABLED})
    with pytest.raises(ValidationError):
        AgentConfig(actions={"get_order": ActionMode.CONFIRM})
    with pytest.raises(ValidationError):
        AgentConfig(actions={"made_up_tool": ActionMode.DISABLED})
    AgentConfig(actions={"create_support_ticket": ActionMode.CONFIRM, "cancel_order": "disabled"})


def test_policy_honours_operator_action_modes() -> None:
    context = _context()
    arguments: dict[str, object] = {"customer_id": 1}
    assert (
        evaluate_default_policy("get_customer_orders", context, arguments).outcome
        == PolicyOutcome.ALLOW
    )
    config = AgentConfig(
        actions={
            "get_customer_orders": ActionMode.DISABLED,
            "create_support_ticket": ActionMode.CONFIRM,
        }
    )
    with use_agent_config(config):
        denied = evaluate_default_policy("get_customer_orders", context, arguments)
        assert denied.outcome == PolicyOutcome.DENY
        assert denied.reasons == ["action_disabled_by_operator"]
        confirm = evaluate_default_policy("create_support_ticket", context, arguments)
        assert confirm.outcome == PolicyOutcome.REQUIRE_CONFIRMATION
        # Ownership still wins over configuration.
        other = evaluate_default_policy("cancel_order", context, {"customer_id": 2})
        assert other.reasons == ["ownership_required"]


def test_guidance_prompt_only_when_rules_are_enabled() -> None:
    assert guidance_prompt() is None
    rule = GuidanceRule(
        id="g1",
        title="Chargebacks",
        instruction="Escalate chargebacks as high priority.",
        intents=[Intent.HUMAN_ESCALATION],
    )
    with use_agent_config(AgentConfig(guidance=[rule.model_copy(update={"enabled": False})])):
        assert guidance_prompt() is None
    with use_agent_config(AgentConfig(guidance=[rule])):
        prompt = guidance_prompt()
        assert prompt is not None
        assert "[human_escalation] Escalate chargebacks" in prompt
        assert "never grants authority" in prompt


def test_draft_publish_restore_lifecycle(client: TestClient) -> None:
    state = client.get("/ui/agent-config").json()
    assert state["published_version"] is None and state["draft"] is None

    draft = {"guidance": [], "actions": {"get_customer_orders": "disabled"}}
    state = client.put(
        "/ui/agent-config/draft", json={"config": draft, "note": "pause lookups"}
    ).json()
    assert state["draft_version"] == 1 and state["published"]["actions"] == {}

    state = client.post("/ui/agent-config/publish", json={"note": ""}).json()
    assert state["published_version"] == 1 and state["draft"] is None
    assert client.post("/ui/agent-config/publish", json={}).status_code == 409

    state = client.put("/ui/agent-config/draft", json={"config": {"actions": {}}}).json()
    state = client.post("/ui/agent-config/publish", json={}).json()
    assert state["published_version"] == 2
    assert [v["status"] for v in state["versions"]] == ["published", "archived"]

    state = client.post("/ui/agent-config/versions/1/restore").json()
    assert state["draft"]["actions"] == {"get_customer_orders": "disabled"}
    assert client.delete("/ui/agent-config/draft").json()["draft"] is None

    invalid = {"config": {"actions": {"escalate_to_human": "disabled"}}}
    assert client.put("/ui/agent-config/draft", json=invalid).status_code == 422


def test_runtime_reads_published_config(client: TestClient) -> None:
    runtime = AgentRuntime(provider=FakeDecisionProvider([_orders(), _orders()]))
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    try:
        chat = {"conversation_id": "studio-runtime", "customer_id": 1, "message": "My orders?"}
        assert client.post("/agent/chat", json=chat).json()["tool_call"]["status"] == "executed"

        client.put(
            "/ui/agent-config/draft",
            json={"config": {"actions": {"get_customer_orders": "disabled"}}},
        )
        # A draft is not live.
        invalidate_cache()
        client.post("/ui/agent-config/publish", json={})
        response = client.post(
            "/agent/chat", json={**chat, "conversation_id": "studio-runtime-2"}
        ).json()
        assert (response["tool_call"] or {}).get("status") != "executed"
    finally:
        app.dependency_overrides.pop(get_agent_runtime, None)

    actions = {item["name"]: item for item in client.get("/ui/actions").json()}
    assert actions["get_customer_orders"]["published_mode"] == "disabled"
    assert actions["get_customer_orders"]["usage"]["calls"] >= 1
    assert actions["get_customer_orders"]["usage"]["denied"] >= 1
    assert actions["escalate_to_human"]["locked"] is True
    assert actions["cancel_order"]["allowed_modes"] == ["enabled", "disabled"]
    assert "Explicit customer confirmation" in actions["cancel_order"]["safeguards"]

    procedures = {item["intent"]: item for item in client.get("/ui/procedures").json()}
    assert procedures["order_list"]["usage_runs"] >= 1
    assert procedures["refund_request"]["actions"] == ["request_refund"]
    lookup_step = next(s for s in procedures["order_list"]["steps"] if s["action"])
    assert "Switched off" in lookup_step["operator_note"]

    client.put(
        "/ui/agent-config/draft",
        json={"config": {"actions": {"create_support_ticket": "confirm"}}},
    )
    ticket = {item["intent"]: item for item in client.get("/ui/procedures").json()}["ticket_create"]
    assert [step["kind"] for step in ticket["steps"]].count("confirm") >= 1


def test_knowledge_articles_edit_search_archive_and_revert(
    client: TestClient, db_session: Session
) -> None:
    documents = client.get("/ui/knowledge").json()
    assert {"refund-policy", "shipping-policy"} <= {item["document_id"] for item in documents}
    assert all(item["origin"] == "built_in" for item in documents)

    created = client.post(
        "/ui/knowledge",
        json={"title": "Gift cards", "content": "## Balance\n\nGift card balances never expire."},
    )
    assert created.status_code == 201
    assert created.json()["document_id"] == "gift-cards"

    hits = client.post(
        "/ui/knowledge/search", json={"query": "do gift card balances expire"}
    ).json()
    assert hits["hits"][0]["document_id"] == "gift-cards"

    edited = client.put(
        "/ui/knowledge/refund-policy",
        json={"title": "Refund Policy", "content": "## Eligibility\n\nRefunds within 45 days."},
    ).json()
    assert "45 days" in edited["content"]
    listed = {item["document_id"]: item for item in client.get("/ui/knowledge").json()}
    assert listed["refund-policy"]["edited_in_console"] is True
    assert listed["gift-cards"]["origin"] == "console"

    client.post("/ui/knowledge/gift-cards/archive")
    assert "gift-cards" not in {doc.document_id for doc in knowledge.corpus(db_session)}
    client.post("/ui/knowledge/gift-cards/unarchive")
    assert "gift-cards" in {doc.document_id for doc in knowledge.corpus(db_session)}

    reverted = client.post("/ui/knowledge/refund-policy/revert").json()
    assert "45 days" not in reverted["content"]
    assert client.post("/ui/knowledge/gift-cards/revert").status_code == 409
    assert client.get("/ui/knowledge/nope").status_code == 404


def test_knowledge_publish_never_calls_paid_embeddings(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    status = client.get("/ui/knowledge/status").json()
    assert status["publish_allowed"] is False
    assert "paid" in status["publish_blocked_reason"]
    assert client.post("/ui/knowledge/publish").status_code == 409


def test_studio_editor_role_guards_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "studio_editor_role", "agent_admin")
    assert client.get("/ui/agent-config").status_code == 200
    assert client.put("/ui/agent-config/draft", json={"config": {}}).status_code == 403
    assert client.post("/ui/knowledge", json={"title": "x", "content": "y"}).status_code == 403
