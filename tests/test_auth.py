import logging

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.backends import StaticBearerAuthenticator
from app.auth.dependencies import (
    build_authenticator,
    get_authenticator,
    get_current_principal,
    require_role,
)
from app.auth.models import (
    ActorType,
    AuthenticationCredentials,
    CredentialScheme,
    Principal,
    PrincipalType,
)
from app.auth.oidc import OIDCAuthenticator
from app.auth.protocols import AuthenticationConfigurationError, InvalidCredentialsError
from app.core.config import AuthenticationMode, Settings

RAW_TOKEN = "local-operator-token"


def _principal() -> Principal:
    return Principal(
        actor_id="operator-local",
        actor_type=ActorType.SUPPORT_OPERATOR,
        roles=["support_operator", "memory_reader"],
        credential_id="local-operator",
    )


def _authenticator() -> StaticBearerAuthenticator:
    return StaticBearerAuthenticator({RAW_TOKEN: _principal()})


def _credentials(token: str) -> AuthenticationCredentials:
    return AuthenticationCredentials(scheme=CredentialScheme.BEARER, secret=SecretStr(token))


def _test_app(authenticator: StaticBearerAuthenticator) -> FastAPI:
    application = FastAPI()
    application.dependency_overrides[get_authenticator] = lambda: authenticator

    @application.get("/principal")
    def principal(current: Principal = Depends(get_current_principal)) -> Principal:
        return current

    @application.get("/operator")
    def operator(current: Principal = Depends(require_role("support_operator"))) -> Principal:
        return current

    return application


def test_valid_token_resolves_correct_principal() -> None:
    principal = _authenticator().authenticate(_credentials(RAW_TOKEN))

    assert principal == _principal()
    assert principal.actor_type == ActorType.SUPPORT_OPERATOR


def test_invalid_token_fails_closed() -> None:
    with pytest.raises(InvalidCredentialsError, match="Authentication failed"):
        _authenticator().authenticate(_credentials("invalid-token"))


def test_missing_token_fails_with_bearer_challenge() -> None:
    with TestClient(_test_app(_authenticator())) as client:
        response = client.get("/principal")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_multiple_roles_serialize_correctly() -> None:
    payload = _principal().model_dump(mode="json")

    assert payload["roles"] == ["support_operator", "memory_reader"]
    assert payload["actor_type"] == "support_operator"


def test_role_dependency_accepts_matching_role() -> None:
    with TestClient(_test_app(_authenticator())) as client:
        response = client.get("/operator", headers={"Authorization": f"Bearer {RAW_TOKEN}"})

    assert response.status_code == 200
    assert response.json()["actor_id"] == "operator-local"


def test_raw_token_never_appears_in_errors_logs_or_responses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    invalid_token = "never-expose-this-token"
    with caplog.at_level(logging.DEBUG), pytest.raises(InvalidCredentialsError) as captured:
        _authenticator().authenticate(_credentials(invalid_token))

    with TestClient(_test_app(_authenticator())) as client:
        response = client.get("/principal", headers={"Authorization": f"Bearer {invalid_token}"})

    assert invalid_token not in str(captured.value)
    assert invalid_token not in repr(captured.value)
    assert invalid_token not in caplog.text
    assert invalid_token not in response.text
    assert response.status_code == 401


def test_principal_model_does_not_expose_secrets() -> None:
    principal = _principal()
    serialized = principal.model_dump(mode="json")

    assert "token" not in serialized
    assert "secret" not in serialized
    assert RAW_TOKEN not in repr(principal)
    assert RAW_TOKEN not in principal.model_dump_json()


def test_static_configuration_is_secret_safe() -> None:
    configuration = SecretStr(
        '{"local-operator-token":{"actor_id":"operator-local",'
        '"actor_type":"support_operator","roles":["support_operator"]}}'
    )
    authenticator = StaticBearerAuthenticator.from_json(configuration)

    assert authenticator.authenticate(_credentials(RAW_TOKEN)).actor_id == "operator-local"
    assert RAW_TOKEN not in repr(configuration)
    assert RAW_TOKEN not in repr(authenticator)


def test_invalid_configuration_does_not_echo_configuration() -> None:
    invalid_configuration = "sensitive-invalid-configuration"

    with pytest.raises(AuthenticationConfigurationError) as captured:
        StaticBearerAuthenticator.from_json(invalid_configuration)

    assert invalid_configuration not in str(captured.value)
    assert invalid_configuration not in repr(captured.value)


def test_local_demo_authentication_resolves_support_operator_principal() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        auth_mode=AuthenticationMode.LOCAL_DEMO,
        local_demo_auth_token=RAW_TOKEN,
        local_demo_actor_id="demo-support",
    )

    principal = build_authenticator(settings).authenticate(_credentials(RAW_TOKEN))

    assert principal == Principal(
        actor_id="demo-support",
        actor_type=ActorType.SUPPORT_OPERATOR,
        principal_type=PrincipalType.LOCAL_DEMO,
        roles=["support_operator"],
        credential_id="local-demo",
        display_name="Demo operator",
    )


def test_local_demo_authentication_still_rejects_invalid_credentials() -> None:
    settings = Settings(
        _env_file=None,
        auth_mode=AuthenticationMode.LOCAL_DEMO,
        local_demo_auth_token=RAW_TOKEN,
    )

    with pytest.raises(InvalidCredentialsError):
        build_authenticator(settings).authenticate(_credentials("wrong-demo-token"))


def test_oidc_mode_builds_production_authenticator_without_discovery_at_startup() -> None:
    settings = Settings(
        _env_file=None,
        auth_mode=AuthenticationMode.OIDC,
        oidc_issuer="https://identity.example.test",
        oidc_audience="agent-control-plane",
    )

    assert isinstance(build_authenticator(settings), OIDCAuthenticator)
