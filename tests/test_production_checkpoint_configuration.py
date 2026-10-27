import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.persistence.checkpoint import (
    MemoryCheckpointProvider,
    PostgresCheckpointProvider,
    build_checkpoint_provider,
)


def production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "production",
        "auth_mode": "oidc",
        "oidc_issuer": "https://identity.example.test",
        "oidc_audience": "checkpoint-test",
        "policy_audit_backend": "postgres",
        "agent_run_projection_backend": "postgres",
        "database_url": "postgresql+psycopg://test:test@localhost/test",
        "checkpoint_backend": "postgres",
    }
    values.update(overrides)
    return Settings.model_validate(
        {key: value for key, value in values.items() if key != "_env_file"}
    )


@pytest.mark.parametrize("environment", ["production", " Production ", "PRODUCTION"])
def test_production_rejects_memory_checkpoints(environment: str) -> None:
    with pytest.raises(ValidationError, match="production requires PostgreSQL checkpoints"):
        production(app_env=environment, checkpoint_backend="memory")


@pytest.mark.parametrize(
    "url", ["sqlite://", "mysql://localhost/test", "postgresql+asyncpg://localhost/test"]
)
def test_production_rejects_unsupported_database_url(url: str) -> None:
    with pytest.raises(ValidationError, match="PostgreSQL database URL"):
        production(database_url=url)


@pytest.mark.parametrize(
    "url",
    ["postgresql://test:test@localhost/test", "postgresql+psycopg://test:test@localhost/test"],
)
def test_production_accepts_durable_postgres_configuration(url: str) -> None:
    provider = build_checkpoint_provider(production(database_url=url))
    assert isinstance(provider, PostgresCheckpointProvider)
    # Construction does not open a connection; initialize() remains a fail-closed startup step.
    assert provider.is_ready() is False
    provider.close()


@pytest.mark.parametrize(
    "override", [{"checkpoint_backend": "memory"}, {"database_url": "sqlite://"}]
)
def test_provider_rechecks_unvalidated_programmatic_configuration(override: dict[str, str]) -> None:
    settings = production().model_copy(update=override)
    with pytest.raises(ValueError, match="production"):
        build_checkpoint_provider(settings)


def test_development_retains_explicit_memory_checkpoints() -> None:
    settings = Settings(_env_file=None, app_env="development", checkpoint_backend="memory")
    assert isinstance(build_checkpoint_provider(settings), MemoryCheckpointProvider)
