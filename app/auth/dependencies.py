from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.backends import DisabledAuthenticator, StaticBearerAuthenticator
from app.auth.models import (
    ActorType,
    AuthenticationCredentials,
    CredentialScheme,
    CustomerScope,
    Principal,
    PrincipalType,
)
from app.auth.oidc import OIDCAuthenticator, OIDCConfiguration
from app.auth.protocols import (
    AuthenticationError,
    AuthenticationFailureReason,
    Authenticator,
)
from app.core.config import AuthenticationMode, Settings, get_settings
from app.observability.metrics import get_metrics

_bearer = HTTPBearer(auto_error=False)


@lru_cache
def get_authenticator() -> Authenticator:
    return build_authenticator(get_settings())


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_mode == AuthenticationMode.DISABLED:
        return DisabledAuthenticator()
    if settings.auth_mode == AuthenticationMode.LOCAL_DEMO:
        assert settings.local_demo_auth_token is not None
        return StaticBearerAuthenticator(
            {
                settings.local_demo_auth_token.get_secret_value(): Principal(
                    actor_id=settings.local_demo_actor_id,
                    actor_type=ActorType.SUPPORT_OPERATOR,
                    principal_type=PrincipalType.LOCAL_DEMO,
                    roles=["support_operator"],
                    credential_id="local-demo",
                    display_name=settings.local_demo_operator_name,
                )
            }
        )
    if settings.auth_mode == AuthenticationMode.OIDC:
        assert settings.oidc_issuer is not None
        assert settings.oidc_audience is not None
        return OIDCAuthenticator(
            OIDCConfiguration(
                issuer=settings.oidc_issuer,
                audience=settings.oidc_audience,
                discovery_url=settings.oidc_discovery_url,
                algorithm=settings.oidc_algorithm,
                roles_claim=settings.oidc_roles_claim,
                groups_claim=settings.oidc_groups_claim,
                tenant_claim=settings.oidc_tenant_claim,
                name_claim=settings.oidc_name_claim,
                customer_scope_claim=settings.oidc_customer_scope_claim,
                email_claim=settings.oidc_email_claim,
                support_role=settings.oidc_support_role,
                customer_role=settings.oidc_customer_role,
                service_role=settings.oidc_service_role,
                require_tenant=settings.oidc_require_tenant,
                require_customer_scope=settings.oidc_require_customer_scope,
                http_timeout_seconds=settings.oidc_http_timeout_seconds,
                jwks_cache_ttl_seconds=settings.oidc_jwks_cache_ttl_seconds,
                clock_skew_seconds=settings.oidc_clock_skew_seconds,
            )
        )
    return StaticBearerAuthenticator.from_json(settings.auth_tokens_json)


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    authenticator: Authenticator = Depends(get_authenticator),
) -> Principal:
    if credentials is None:
        _record_authentication(
            success=False,
            failure_reason=AuthenticationFailureReason.MISSING_CREDENTIALS,
            principal_type="unknown",
        )
        raise _authentication_error("Authentication required.")
    try:
        principal = authenticator.authenticate(
            AuthenticationCredentials(
                scheme=CredentialScheme(credentials.scheme.casefold()),
                secret=credentials.credentials,
            )
        )
        _record_authentication(
            success=True,
            failure_reason=None,
            principal_type=principal.principal_type.value,
        )
        return principal
    except AuthenticationError as error:
        _record_authentication(
            success=False,
            failure_reason=error.reason,
            principal_type="unknown",
        )
        raise _authentication_error("Invalid authentication credentials.") from None
    except ValueError:
        _record_authentication(
            success=False,
            failure_reason=AuthenticationFailureReason.INVALID_CREDENTIALS,
            principal_type="unknown",
        )
        raise _authentication_error("Invalid authentication credentials.") from None


def require_role(role: str) -> Callable[[Principal], Principal]:
    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if role not in principal.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
            )
        return principal

    return dependency


def require_support_operator(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    if (
        principal.actor_type != ActorType.SUPPORT_OPERATOR
        or "support_operator" not in principal.roles
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions.",
        )
    return principal


def resolve_customer_scope(
    principal: Principal, requested_customer_id: int | None
) -> CustomerScope:
    """Resolve a caller-supplied customer ID against authenticated identity."""

    if principal.actor_type == ActorType.CUSTOMER:
        if principal.customer_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Customer identity is not scoped.",
            )
        if requested_customer_id is not None and requested_customer_id != principal.customer_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )
        return CustomerScope(customer_id=principal.customer_id, principal=principal)
    if principal.actor_type == ActorType.SUPPORT_OPERATOR:
        if "support_operator" not in principal.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
            )
        if requested_customer_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Customer scope is required.",
            )
        if (
            principal.principal_type == PrincipalType.OIDC
            and requested_customer_id not in principal.customer_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )
        return CustomerScope(customer_id=requested_customer_id, principal=principal)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions.",
    )


def _authentication_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _record_authentication(
    *,
    success: bool,
    failure_reason: AuthenticationFailureReason | None,
    principal_type: str,
) -> None:
    get_metrics().authentication_attempts_total.add(
        1,
        {
            "auth_success": success,
            "auth_failure_reason": failure_reason.value if failure_reason else "none",
            "principal_type": principal_type,
        },
    )
