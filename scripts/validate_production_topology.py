"""Validate the repository's production-oriented Compose topology without starting services."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REQUIRED_SERVICES = {"db", "demo-setup", "backend", "frontend", "qdrant", "jaeger"}
HARDENED_SERVICES = {"db", "backend", "frontend", "qdrant", "jaeger"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-health", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args()

    rendered = _render_compose()
    services = rendered.get("services")
    if not isinstance(services, Mapping):
        return _fail("production Compose did not render services")
    missing = REQUIRED_SERVICES - set(services)
    if missing:
        return _fail(f"production topology is missing services: {sorted(missing)}")

    for service_name in HARDENED_SERVICES:
        service = services[service_name]
        if not isinstance(service, Mapping):
            return _fail(f"service {service_name} is not a mapping")
        if service.get("read_only") is not True:
            return _fail(f"service {service_name} is not read-only")
        if "ALL" not in service.get("cap_drop", []):
            return _fail(f"service {service_name} does not drop all capabilities")
        if "no-new-privileges:true" not in service.get("security_opt", []):
            return _fail(f"service {service_name} does not set no-new-privileges")
        if not service.get("restart") or not service.get("stop_grace_period"):
            return _fail(f"service {service_name} lacks restart/shutdown policy")
        limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
        if not limits.get("cpus") or not limits.get("memory"):
            return _fail(f"service {service_name} lacks CPU and memory limits")

    for service_name in ("db", "qdrant", "jaeger"):
        if not services[service_name].get("healthcheck"):
            return _fail(f"service {service_name} lacks a healthcheck")

    port_error = validate_published_ports(services)
    if port_error is not None:
        return _fail(port_error)

    if args.evidence_root is not None and not _check_local_evidence_root(args.evidence_root):
        return _fail("local evidence root is not an accessible directory")

    backend_env = services["backend"].get("environment", {})
    if backend_env.get("AUTH_MODE") != "oidc":
        return _fail("production backend authentication is not OIDC")
    if backend_env.get("LOCAL_DEMO_AUTH_TOKEN") not in (None, ""):
        return _fail("local demo credential is present in production backend configuration")
    frontend_args = services["frontend"].get("build", {}).get("args", {})
    if frontend_args.get("FRONTEND_AUTH_MODE") != "external_session":
        return _fail("production frontend authentication is not external_session")
    if frontend_args.get("LOCAL_DEMO_AUTH_TOKEN") not in (None, ""):
        return _fail("local demo credential is present in production frontend configuration")

    if args.check_health:
        for path in ("/health", "/ready"):
            try:
                with urlopen(f"{args.base_url.rstrip('/')}{path}", timeout=5) as response:
                    if response.status != 200:
                        return _fail(f"{path} returned HTTP {response.status}")
            except (OSError, URLError) as error:
                return _fail(f"{path} health check failed: {type(error).__name__}")

    print("production topology validation: PASS")
    return 0


def validate_published_ports(services: Mapping[str, object]) -> str | None:
    """Only frontend ingress and loopback Prometheus inspection may be published."""
    for name in ("db", "qdrant", "jaeger", "backend", "demo-setup", "otel-collector"):
        service = services.get(name)
        if isinstance(service, Mapping) and service.get("ports"):
            return f"internal service {name} publishes host ports"
    frontend = services.get("frontend")
    if not isinstance(frontend, Mapping):
        return "frontend service is missing"
    ports = frontend.get("ports", [])
    if not isinstance(ports, list) or len(ports) != 1:
        return "frontend must publish exactly one ingress port"
    port = ports[0]
    if not isinstance(port, Mapping) or port.get("target") != 8080 or port.get("protocol") != "tcp":
        return "frontend may publish only its HTTP ingress"
    prometheus = services.get("prometheus")
    if isinstance(prometheus, Mapping):
        for port in prometheus.get("ports", []):
            if (
                not isinstance(port, Mapping)
                or port.get("host_ip") not in {"127.0.0.1", "::1"}
                or port.get("target") != 9090
                or port.get("protocol") != "tcp"
            ):
                return "Prometheus inspection port must be loopback-only TCP/9090"
    return None


def _render_compose() -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_PASSWORD": "validation-only",
            "DATABASE_URL": "postgresql+psycopg://app:validation-only@db:5432/customer_service",
            "OIDC_ISSUER": "https://identity.validation.invalid",
            "OIDC_AUDIENCE": "agent-control-plane",
            "DEPLOYMENT_ID": "topology-validation",
            "EVIDENCE_STORE_BACKEND": "s3",
            "EVIDENCE_STORE_BUCKET": "validation-evidence",
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "metrics",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.prod.yml",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("docker compose production configuration could not be rendered")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("docker compose returned invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("docker compose configuration is not an object")
    return value


def _fail(message: str) -> int:
    print(f"production topology validation: FAIL ({message})", file=sys.stderr)
    return 1


def _check_local_evidence_root(root: Path) -> bool:
    return root.is_dir() and os.access(root, os.R_OK | os.W_OK)


if __name__ == "__main__":
    raise SystemExit(main())
