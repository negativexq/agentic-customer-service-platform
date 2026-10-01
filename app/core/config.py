import json
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.auth.models import Principal


class AuthenticationMode(StrEnum):
    DISABLED = "disabled"
    LOCAL_DEMO = "local_demo"
    STATIC = "static"
    OIDC = "oidc"


class LLMProvider(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    DETERMINISTIC_INTEGRATION = "deterministic_integration"


LLMReasoningEffort = Literal["none", "low", "medium", "high"]
LLMStructuredOutputMode = Literal["schema", "function_calling"]
DecisionContractVersion = Literal["direct_tool_v1", "semantic_decision_v2", "semantic_decision_v3"]


class Settings(BaseSettings):
    app_name: str = Field(default="Agentic Customer Service Platform")
    service_version: str = Field(default="0.1.0", min_length=1, max_length=64)
    deployment_id: str = Field(default="local", min_length=1, max_length=200)
    app_env: str = Field(default="development")
    debug: bool = False
    database_url: str = Field(
        default="postgresql+psycopg://app:app@localhost:5432/customer_service"
    )
    llm_model: str = "llama3.1"
    llm_provider: LLMProvider = LLMProvider.OPENAI_COMPATIBLE
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_reasoning_effort: LLMReasoningEffort | None = None
    llm_structured_output_mode: LLMStructuredOutputMode = "schema"
    agent_decision_contract_version: DecisionContractVersion = "semantic_decision_v3"
    llm_connect_timeout_seconds: float = Field(default=5.0, gt=0.0)
    llm_timeout_seconds: float = Field(default=30.0, gt=0.0)
    confirmation_ttl_seconds: int = Field(default=300, gt=0)
    rag_backend: str = Field(default="qdrant", pattern="^(qdrant|local)$")
    embedding_provider: str = Field(
        default="deterministic", pattern="^(deterministic|openai|huggingface)$"
    )
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = Field(default=32, gt=0)
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_connect_timeout_seconds: float = Field(default=5.0, gt=0.0)
    embedding_timeout_seconds: float = Field(default=30.0, gt=0.0)
    reranker_enabled: bool = True
    qdrant_url: str = "http://localhost:6333"
    # Logical alias; physical snapshot collections are managed by rag_ingest.py.
    qdrant_collection: str = "customer_service_knowledge"
    qdrant_schema_version: int = Field(default=2, gt=0)
    qdrant_chunking_version: int = Field(default=1, gt=0)
    rag_dense_top_k: int = Field(default=8, gt=0)
    rag_sparse_top_k: int = Field(default=8, gt=0)
    rag_rerank_candidates: int = Field(default=12, gt=0)
    rag_final_context_count: int = Field(default=4, gt=0)
    rag_chunk_size: int = Field(default=800, gt=100)
    rag_reranker_timeout_seconds: float = Field(default=3.0, gt=0.0)
    qdrant_timeout_seconds: float = Field(default=10.0, gt=0.0)
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "agentic-customer-service-platform"
    otel_metrics_enabled: bool = False
    otel_exporter_otlp_metrics_endpoint: str = ""
    otel_metric_export_interval_millis: int = Field(default=15000, ge=1000, le=300000)
    otel_metric_export_timeout_millis: int = Field(default=5000, ge=1, le=30000)
    memory_enabled: bool = True
    memory_max_context_items: int = Field(default=5, gt=0, le=20)
    memory_default_ttl_days: int = Field(default=365, ge=0)
    memory_support_context_ttl_days: int = Field(default=30, gt=0)
    resilience_enabled: bool = True
    resilience_max_retries: int = Field(default=2, ge=0, le=5)
    resilience_initial_backoff_ms: int = Field(default=100, ge=0, le=5000)
    resilience_max_backoff_ms: int = Field(default=500, ge=0, le=10000)
    resilience_jitter_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    resilience_retry_budget_attempts: int = Field(default=100, gt=0, le=10000)
    resilience_retry_budget_window_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    resilience_max_retry_after_seconds: float = Field(default=30.0, ge=0.0, le=300.0)
    resilience_circuit_failure_threshold: int = Field(default=5, gt=0, le=100)
    resilience_circuit_recovery_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    resilience_circuit_half_open_attempts: int = Field(default=1, gt=0, le=10)
    resilience_bulkhead_default_limit: int = Field(default=32, gt=0, le=1000)
    resilience_bulkhead_provider_limit: int = Field(default=8, gt=0, le=1000)
    resilience_bulkhead_wait_seconds: float = Field(default=0.0, ge=0.0, le=30.0)
    resilience_principal_rate_limit: int = Field(default=600, gt=0, le=100000)
    resilience_customer_rate_limit: int = Field(default=600, gt=0, le=100000)
    resilience_provider_rate_limit: int = Field(default=600, gt=0, le=100000)
    resilience_rate_limit_window_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    retrieval_timeout_seconds: float = Field(default=5.0, gt=0.0)
    tool_timeout_seconds: float = Field(default=10.0, gt=0.0)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0.0)
    database_query_timeout_seconds: float = Field(default=10.0, gt=0.0)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0.0)
    auth_mode: AuthenticationMode = AuthenticationMode.DISABLED
    local_demo_auth_token: SecretStr | None = None
    local_demo_actor_id: str = Field(default="operator-local-demo", min_length=1, max_length=200)
    auth_tokens_json: SecretStr = SecretStr("{}")
    oidc_issuer: str | None = Field(default=None, min_length=1, max_length=500)
    oidc_audience: str | None = Field(default=None, min_length=1, max_length=500)
    oidc_discovery_url: str | None = Field(default=None, min_length=1, max_length=1000)
    oidc_algorithm: Literal["RS256", "RS384", "RS512"] = "RS256"
    oidc_roles_claim: str = Field(default="roles", min_length=1, max_length=100)
    oidc_groups_claim: str = Field(default="groups", min_length=1, max_length=100)
    oidc_tenant_claim: str = Field(default="tenant_id", min_length=1, max_length=100)
    oidc_customer_scope_claim: str = Field(default="customer_ids", min_length=1, max_length=100)
    oidc_email_claim: str = Field(default="email", min_length=1, max_length=100)
    oidc_support_role: str = Field(default="support_operator", min_length=1, max_length=100)
    oidc_customer_role: str = Field(default="customer", min_length=1, max_length=100)
    oidc_service_role: str = Field(default="service", min_length=1, max_length=100)
    oidc_require_tenant: bool = True
    oidc_require_customer_scope: bool = True
    oidc_http_timeout_seconds: float = Field(default=5.0, gt=0.0, le=30.0)
    oidc_jwks_cache_ttl_seconds: int = Field(default=300, ge=30, le=86400)
    oidc_clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    checkpoint_backend: str = Field(default="postgres", pattern="^(postgres|memory)$")
    policy_audit_backend: str = Field(default="postgres", pattern="^(postgres|memory)$")
    policy_audit_memory_limit: int = Field(default=500, gt=0, le=5000)
    policy_audit_query_limit: int = Field(default=50, gt=0, le=100)
    agent_run_projection_backend: str = Field(default="memory", pattern="^(postgres|memory)$")
    agent_run_projection_memory_limit: int = Field(default=500, gt=0, le=5000)
    agent_run_projection_query_limit: int = Field(default=50, gt=0, le=100)
    evidence_store_backend: str = Field(default="local", pattern="^(local|s3)$")
    evidence_store_root: str = Field(default="artifacts/evidence-payloads", min_length=1)
    evidence_store_bucket: str | None = Field(default=None, min_length=1, max_length=200)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def normalize_empty_reasoning_effort(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("oidc_issuer", "oidc_audience", "oidc_discovery_url", mode="before")
    @classmethod
    def normalize_empty_oidc_settings(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_authentication_mode(self) -> "Settings":
        environment = self.app_env.casefold()
        if self.auth_mode == AuthenticationMode.LOCAL_DEMO:
            if environment not in {"development", "demo", "test", "integration"}:
                raise ValueError(
                    "local_demo authentication is restricted to development/demo/integration"
                )
            if (
                self.local_demo_auth_token is None
                or not self.local_demo_auth_token.get_secret_value()
            ):
                raise ValueError("local_demo authentication requires LOCAL_DEMO_AUTH_TOKEN")
        if self.auth_mode == AuthenticationMode.STATIC:
            try:
                configured_tokens = json.loads(self.auth_tokens_json.get_secret_value())
                if (
                    not isinstance(configured_tokens, dict)
                    or not configured_tokens
                    or any(not isinstance(token, str) or not token for token in configured_tokens)
                ):
                    raise TypeError
                for configured_principal in configured_tokens.values():
                    Principal.model_validate(configured_principal)
            except (json.JSONDecodeError, TypeError, ValidationError):
                raise ValueError(
                    "static authentication requires a non-empty AUTH_TOKENS_JSON"
                ) from None
        if self.auth_mode == AuthenticationMode.OIDC:
            if not self.oidc_issuer or not self.oidc_audience:
                raise ValueError("OIDC authentication requires OIDC_ISSUER and OIDC_AUDIENCE")
            if environment == "production" and (
                not self.oidc_issuer.startswith("https://")
                or (
                    self.oidc_discovery_url is not None
                    and not self.oidc_discovery_url.startswith("https://")
                )
            ):
                raise ValueError("production OIDC discovery requires HTTPS")
        if environment == "production" and self.auth_mode != AuthenticationMode.OIDC:
            raise ValueError("production requires OIDC authentication")
        if environment in {"production", "integration"} and self.policy_audit_backend != "postgres":
            raise ValueError("production and integration require PostgreSQL policy audit storage")
        if (
            environment in {"production", "integration"}
            and self.agent_run_projection_backend != "postgres"
        ):
            raise ValueError(
                "production and integration require PostgreSQL agent-run projection storage"
            )
        if self.llm_provider == LLMProvider.DETERMINISTIC_INTEGRATION and (
            environment != "integration" or self.auth_mode != AuthenticationMode.LOCAL_DEMO
        ):
            raise ValueError(
                "deterministic_integration LLM provider requires "
                "APP_ENV=integration and AUTH_MODE=local_demo"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
