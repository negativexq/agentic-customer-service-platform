"""Signed OIDC HTTP regressions for operator customer scope (issue #16)."""

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.dependencies import get_authenticator
from app.auth.models import ActorType, Principal, PrincipalType
from app.auth.oidc import OIDCAuthenticator, OIDCConfiguration
from app.core.config import get_settings
from app.main import app
from app.memory.models import MemoryRecord
from app.memory.service import MemoryService
from app.policies.models import PolicyAuditEvent, PolicyOutcome
from app.policies.repository import SqlAlchemyPolicyAuditRepository
from app.ui.repository import (
    AgentRunProjectionRepository,
    InMemoryAgentRunProjectionRepository,
    SqlAlchemyAgentRunProjectionRepository,
)
from app.ui.schemas import AgentRunView, UIMemoryUsage


@pytest.fixture
def oidc_client(client: TestClient) -> Iterator[TestClient]:
    issuer = "https://identity.example.test"
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="scope-test", use="sig", alg="RS256")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": issuer, "jwks_uri": f"{issuer}/keys"})
        return httpx.Response(200, json={"keys": [public]})

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    auth = OIDCAuthenticator(
        OIDCConfiguration(issuer=issuer, audience="scope-test"), http_client=transport
    )
    app.dependency_overrides[get_authenticator] = lambda: auth
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": "scope-test",
            "sub": "operator",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
            "roles": ["support_operator"],
            "tenant_id": "default",
            "customer_ids": [1],
        },
        private,
        algorithm="RS256",
        headers={"kid": "scope-test"},
    )
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
    transport.close()


@pytest.fixture(params=["memory", "sql"])
def scoped_runs(
    request: pytest.FixtureRequest, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> AgentRunProjectionRepository:
    repo: AgentRunProjectionRepository = (
        InMemoryAgentRunProjectionRepository()
        if request.param == "memory"
        else SqlAlchemyAgentRunProjectionRepository(db_session)
    )
    for index, (customer, tenant) in enumerate(((1, "default"), (2, "default"), (1, "other"))):
        repo.upsert(
            AgentRunView(
                run_id=f"scope-run-{index}",
                request_id=f"scope-request-{index}",
                conversation_id="shared-conversation",
                customer_id=customer,
                tenant_id=tenant,
                actor_id="fixture",
                actor_type="support_operator",
                intent="order_lookup",
                request_type="read_action",
                status="completed",
                duration_ms=1,
                started_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
                memory=UIMemoryUsage(item_count=0),
            )
        )
    monkeypatch.setattr("app.api.routes.ui.build_agent_run_projection_repository", lambda *a: repo)
    monkeypatch.setattr(get_settings(), "agent_run_projection_query_limit", 1)
    return repo


def test_lists_filter_before_limit_and_preserve_tenant(
    oidc_client: TestClient, scoped_runs: AgentRunProjectionRepository
) -> None:
    foreign = scoped_runs.get_by_run_id("scope-run-1")
    assert foreign is not None
    scoped_runs.upsert(
        foreign.model_copy(
            update={"run_id": "foreign-only", "conversation_id": "foreign-conversation"}
        )
    )
    denied_conversation = oidc_client.get("/ui/conversations/foreign-conversation")
    assert denied_conversation.status_code == 404
    assert denied_conversation.json() == oidc_client.get("/ui/conversations/missing").json()
    response = oidc_client.get("/ui/agent-runs?limit=1")
    assert response.status_code == 200
    assert [row["run_id"] for row in response.json()] == ["scope-run-0"]
    assert oidc_client.get("/ui/agent-runs?customer_id=2").status_code == 404
    assert oidc_client.get("/customers/2").status_code == 404
    allowed = oidc_client.get("/ui/agent-runs?customer_id=1").json()
    assert [row["run_id"] for row in allowed] == ["scope-run-0"]
    conversation = oidc_client.get("/ui/conversations/shared-conversation")
    assert conversation.status_code == 200
    assert [row["run_id"] for row in conversation.json()["runs"]] == ["scope-run-0"]


@pytest.mark.parametrize("path", ["agent-runs", "tool-events", "rag-events", "traces"])
def test_run_projections_reject_foreign_customer_and_tenant(
    oidc_client: TestClient, scoped_runs: AgentRunProjectionRepository, path: str
) -> None:
    del scoped_runs
    assert oidc_client.get(f"/ui/{path}/scope-run-0").status_code == 200
    missing = oidc_client.get(f"/ui/{path}/missing")
    for run in ("scope-run-1", "scope-run-2"):
        denied = oidc_client.get(f"/ui/{path}/{run}")
        assert denied.status_code == 404
        assert denied.json() == missing.json()


def test_policy_reads_filter_before_limit_without_requiring_projection(
    oidc_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SqlAlchemyPolicyAuditRepository(db_session)
    for index, (customer, tenant) in enumerate(((1, "default"), (2, "default"), (1, "other"))):
        repository.append(
            PolicyAuditEvent(
                event_id=f"scope-event-{index}",
                agent_run_id=f"scope-run-{index}",
                request_id=f"scope-request-{index}",
                conversation_id="shared-conversation",
                tenant_id=tenant,
                actor_id="fixture",
                actor_type=ActorType.SUPPORT_OPERATOR,
                roles=["support_operator"],
                effective_customer_id=customer,
                tool_name="cancel_order",
                risk_level=2,
                policy_outcome=PolicyOutcome.REQUIRE_CONFIRMATION,
                action_id=None,
                reason_codes=["risk_2_confirmation_required"],
                timestamp=datetime.now(UTC),
            )
        )
    monkeypatch.setattr(get_settings(), "policy_audit_query_limit", 1)
    audit = oidc_client.get("/ui/policy-audit/shared-conversation")
    assert audit.status_code == 200
    assert [row["event_id"] for row in audit.json()] == ["scope-event-0"]
    assert oidc_client.get("/ui/policy-events/scope-run-0").status_code == 200
    missing = oidc_client.get("/ui/policy-events/missing")
    for run in ("scope-run-1", "scope-run-2"):
        denied = oidc_client.get(f"/ui/policy-events/{run}")
        assert denied.status_code == 404 and denied.json() == missing.json()
    assert oidc_client.get("/ui/policy-audit/missing").status_code == 404


def test_memory_rejects_customer_outside_oidc_allowlist(
    oidc_client: TestClient, db_session: Session
) -> None:
    db_session.add_all(
        MemoryRecord(
            customer_id=customer,
            memory_type="preference",
            content="Fixture",
            normalized_key="scope-fixture",
            source="user_explicit",
        )
        for customer in (1, 2)
    )
    db_session.commit()
    allowed = oidc_client.get("/ui/memory/1")
    assert allowed.status_code == 200 and len(allowed.json()) == 1
    denied = oidc_client.get("/ui/memory/2")
    assert denied.status_code == 404
    assert denied.json() == oidc_client.get("/ui/memory/999").json()
    principal = Principal(
        actor_id="oidc-operator",
        actor_type=ActorType.SUPPORT_OPERATOR,
        roles=["support_operator"],
        principal_type=PrincipalType.OIDC,
        tenant_id="default",
        customer_ids=[1],
    )
    assert MemoryService().retrieve(db_session, 2, "", principal=principal) == []
