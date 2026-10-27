from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).parents[1]


def test_backend_runtime_is_minimal_non_root_and_sigterm_aware() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime = dockerfile.split(" AS runtime", maxsplit=1)[1]

    assert " AS builder" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert "USER app" in runtime
    assert "STOPSIGNAL SIGTERM" in runtime
    assert "--timeout-graceful-shutdown" in runtime
    assert "COPY --from=uv" not in runtime
    assert "uv sync" not in runtime


def test_compose_uses_readiness_and_production_resource_boundaries() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    # These assertions inspect overlay values; rendered merge semantics have separate tests.
    production = yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text().replace("!reset []", "[]")
    )
    backend_healthcheck = " ".join(compose["services"]["backend"]["healthcheck"]["test"])
    backend = production["services"]["backend"]
    frontend = production["services"]["frontend"]

    assert "/ready" in backend_healthcheck
    assert compose["services"]["frontend"]["depends_on"]["backend"]["condition"] == (
        "service_healthy"
    )
    assert backend["restart"] == "unless-stopped"
    assert backend["read_only"] is True
    assert backend["cap_drop"] == ["ALL"]
    assert backend["deploy"]["resources"]["limits"]
    assert frontend["read_only"] is True
    assert frontend["deploy"]["resources"]["limits"]


def test_frontend_config_applies_security_and_cache_boundaries() -> None:
    nginx = (ROOT / "frontend" / "nginx.conf").read_text()
    headers = (ROOT / "frontend" / "security-headers.conf").read_text()

    assert "Content-Security-Policy" in headers
    assert "X-Content-Type-Options" in headers
    assert "location /assets/" in nginx
    assert "max-age=31536000, immutable" in nginx
    assert 'Cache-Control "no-cache"' in nginx
    assert "try_files $uri $uri/ /index.html" in nginx
    assert "server_tokens off" in nginx
    assert "proxy_set_header Authorization $http_authorization" in nginx
    assert "proxy_set_header Cookie $http_cookie" in nginx
    assert "(agent|customers|orders|tickets|memories|escalations|ui)" in nginx


def test_compose_wires_demo_authentication_and_bootstrap_without_production_inheritance() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    # These assertions inspect overlay values; rendered merge semantics have separate tests.
    production = yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text().replace("!reset []", "[]")
    )

    assert compose["services"]["backend"]["environment"]["AUTH_MODE"] == "local_demo"
    assert compose["services"]["backend"]["environment"]["AGENT_DECISION_CONTRACT_VERSION"] == (
        "${AGENT_DECISION_CONTRACT_VERSION:-semantic_decision_v3}"
    )
    assert compose["services"]["backend"]["depends_on"]["demo-setup"]["condition"] == (
        "service_completed_successfully"
    )
    assert compose["services"]["frontend"]["build"]["args"]["LOCAL_DEMO_AUTH_TOKEN"]
    assert production["services"]["backend"]["environment"]["AUTH_MODE"] == "oidc"
    assert production["services"]["backend"]["environment"]["OIDC_ISSUER"] == (
        "${OIDC_ISSUER:?OIDC_ISSUER must be set}"
    )
    assert production["services"]["backend"]["environment"]["OIDC_AUDIENCE"] == (
        "${OIDC_AUDIENCE:?OIDC_AUDIENCE must be set}"
    )
    assert production["services"]["backend"]["environment"]["LANGGRAPH_STRICT_MSGPACK"] == ("true")
    assert production["services"]["backend"]["environment"]["LLM_PROVIDER"] == ("openai_compatible")
    assert production["services"]["backend"]["environment"]["AGENT_DECISION_CONTRACT_VERSION"] == (
        "semantic_decision_v3"
    )
    assert production["services"]["backend"]["environment"]["LOCAL_DEMO_AUTH_TOKEN"] == ""
    assert production["services"]["backend"]["environment"]["POLICY_AUDIT_BACKEND"] == "postgres"
    assert (
        production["services"]["backend"]["environment"]["AGENT_RUN_PROJECTION_BACKEND"]
        == "postgres"
    )
    assert production["services"]["frontend"]["build"]["args"]["LOCAL_DEMO_AUTH_TOKEN"] == ""
    assert (
        production["services"]["frontend"]["build"]["args"]["FRONTEND_AUTH_MODE"]
        == "external_session"
    )


def test_integration_llm_provider_is_explicit_and_excluded_from_production_overlay() -> None:
    integration = yaml.safe_load((ROOT / "docker-compose.integration.yml").read_text())
    production_text = (ROOT / "docker-compose.prod.yml").read_text()

    assert integration["services"]["backend"]["environment"] == {
        "APP_ENV": "integration",
        "AGENT_DECISION_CONTRACT_VERSION": "semantic_decision_v3",
        "LANGGRAPH_STRICT_MSGPACK": "true",
        "LLM_PROVIDER": "deterministic_integration",
        "LLM_STRUCTURED_OUTPUT_MODE": "function_calling",
        "LLM_REASONING_EFFORT": "none",
        "POLICY_AUDIT_BACKEND": "postgres",
        "AGENT_RUN_PROJECTION_BACKEND": "postgres",
    }
    assert integration["services"]["demo-setup"]["environment"]["APP_ENV"] == "integration"
    assert integration["services"]["demo-setup"]["environment"][
        "AGENT_DECISION_CONTRACT_VERSION"
    ] == ("semantic_decision_v3")
    assert integration["services"]["frontend"]["build"]["args"]["FRONTEND_AUTH_MODE"] == (
        "integration"
    )
    assert (
        integration["services"]["demo-setup"]["environment"]["LANGGRAPH_STRICT_MSGPACK"] == "true"
    )
    assert "deterministic_integration" not in production_text


def test_checkpoint_strict_mode_is_enabled_without_permissive_or_pickle_configuration() -> None:
    compose_text = (ROOT / "docker-compose.yml").read_text()
    integration_text = (ROOT / "docker-compose.integration.yml").read_text()
    production_text = (ROOT / "docker-compose.prod.yml").read_text()

    assert "LANGGRAPH_STRICT_MSGPACK: ${LANGGRAPH_STRICT_MSGPACK:-true}" in compose_text
    assert 'LANGGRAPH_STRICT_MSGPACK: "true"' in integration_text
    assert 'LANGGRAPH_STRICT_MSGPACK: "true"' in production_text
    assert "allowed_msgpack_modules" not in production_text
    assert "pickle_fallback" not in production_text
