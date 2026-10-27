import copy
import os
import shutil
import subprocess
from typing import Any, cast

import pytest

from scripts.validate_production_topology import _render_compose, validate_published_ports


@pytest.fixture(scope="module")
def production_services() -> dict[str, dict[str, Any]]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is required for rendered topology checks")
    return cast(dict[str, dict[str, Any]], _render_compose()["services"])


def test_rendered_production_has_only_frontend_and_loopback_metrics_publications(
    production_services: dict[str, dict[str, Any]],
) -> None:
    assert validate_published_ports(production_services) is None
    for name in ("db", "backend", "qdrant", "jaeger", "otel-collector"):
        assert not production_services[name].get("ports")
    assert production_services["frontend"]["ports"][0]["target"] == 8080
    assert production_services["prometheus"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert "default" in production_services["backend"]["networks"]
    assert "metrics" in production_services["backend"]["networks"]


@pytest.mark.parametrize(
    "service",
    ["db", "backend", "qdrant", "jaeger", "otel-collector", "demo-setup", "unexpected-service"],
)
def test_validator_rejects_even_loopback_publication_for_internal_services(
    production_services: dict[str, dict[str, Any]], service: str
) -> None:
    services = copy.deepcopy(production_services)
    services.setdefault(service, {})["ports"] = [
        {"target": 5432, "published": "15432", "host_ip": "127.0.0.1", "protocol": "tcp"}
    ]
    assert validate_published_ports(services) == f"internal service {service} publishes host ports"


@pytest.mark.parametrize("host", [None, "0.0.0.0", "::", "192.0.2.1"])
def test_metrics_inspection_cannot_bind_publicly(
    production_services: dict[str, dict[str, Any]], host: str | None
) -> None:
    services = copy.deepcopy(production_services)
    services["prometheus"]["ports"][0]["host_ip"] = host
    assert validate_published_ports(services) is not None


def test_frontend_cannot_publish_arbitrary_service_ports(
    production_services: dict[str, dict[str, Any]],
) -> None:
    services = copy.deepcopy(production_services)
    services["frontend"]["ports"][0]["target"] = 22
    assert validate_published_ports(services) is not None
    services["frontend"]["ports"] = []
    assert validate_published_ports(services) is not None


@pytest.mark.parametrize("integration", [False, True])
def test_development_and_integration_keep_their_explicit_host_ports(integration: bool) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is required")
    args = ["docker", "compose", "--env-file", ".env.example", "-f", "docker-compose.yml"]
    if integration:
        args += ["-f", "docker-compose.integration.yml"]
    result = subprocess.run(
        args + ["config", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "LOCAL_DEMO_AUTH_TOKEN": "topology-test-token",
            "LOCAL_DEMO_ACTOR_ID": "topology-test-operator",
        },
    )
    import json

    services = json.loads(result.stdout)["services"]
    for name in ("db", "backend", "qdrant", "jaeger"):
        assert services[name]["ports"], name
