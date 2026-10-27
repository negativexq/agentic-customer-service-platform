from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).parents[1]


def test_frontend_auth_modes_are_explicit_and_production_is_external_session() -> None:
    integration = yaml.safe_load((ROOT / "docker-compose.integration.yml").read_text())
    # These assertions inspect overlay values; rendered merge semantics have separate tests.
    production = yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text().replace("!reset []", "[]")
    )
    vite = (ROOT / "frontend" / "vite.config.ts").read_text()

    assert integration["services"]["frontend"]["build"]["args"]["FRONTEND_AUTH_MODE"] == (
        "integration"
    )
    assert production["services"]["frontend"]["build"]["args"]["FRONTEND_AUTH_MODE"] == (
        "external_session"
    )
    assert '"external_session"' in vite
    assert "VITE_PRODUCTION_BEARER_TOKEN" not in vite
    assert "VITE_STATIC_AUTH_TOKEN" not in vite


def test_production_bundle_has_no_credential_sentinels_when_present() -> None:
    assets = ROOT / "frontend" / "dist" / "assets"
    if not assets.exists():
        pytest.skip("production frontend bundle has not been built")
    content = "\n".join(
        path.read_text(errors="ignore") for path in assets.glob("*") if path.is_file()
    )
    for sentinel in (
        "local-demo-support-token",
        "integration-test-token",
        "PRODUCTION_STATIC_TOKEN_SENTINEL",
        "VITE_PRODUCTION_BEARER_TOKEN",
        "VITE_STATIC_AUTH_TOKEN",
    ):
        assert sentinel not in content
