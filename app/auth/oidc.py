"""OIDC discovery and JWT validation for the production authentication boundary."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from app.auth.models import (
    ActorType,
    AuthenticationCredentials,
    CredentialScheme,
    Principal,
    PrincipalType,
)
from app.auth.protocols import (
    AuthenticationFailureReason,
    AuthenticationUnavailableError,
    InvalidCredentialsError,
)


@dataclass(frozen=True, slots=True)
class OIDCConfiguration:
    issuer: str
    audience: str
    discovery_url: str | None = None
    algorithm: str = "RS256"
    roles_claim: str = "roles"
    groups_claim: str = "groups"
    tenant_claim: str = "tenant_id"
    customer_scope_claim: str = "customer_ids"
    email_claim: str = "email"
    name_claim: str = "name"
    support_role: str = "support_operator"
    customer_role: str = "customer"
    service_role: str = "service"
    require_tenant: bool = True
    require_customer_scope: bool = True
    http_timeout_seconds: float = 5.0
    jwks_cache_ttl_seconds: int = 300
    clock_skew_seconds: int = 30


class OIDCAuthenticator:
    """Validate bearer JWTs against issuer-discovered, rotation-aware JWKS keys."""

    def __init__(
        self,
        configuration: OIDCConfiguration,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._configuration = configuration
        self._http_client = http_client or httpx.Client(
            timeout=configuration.http_timeout_seconds,
            follow_redirects=False,
        )
        self._lock = threading.RLock()
        self._jwks_uri: str | None = None
        self._keys: dict[str, Any] = {}
        self._keys_expires_at = 0.0

    def authenticate(self, credentials: AuthenticationCredentials) -> Principal:
        if credentials.scheme != CredentialScheme.BEARER:
            raise InvalidCredentialsError()
        token = credentials.secret.get_secret_value()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise InvalidCredentialsError() from None
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm != self._configuration.algorithm or not isinstance(key_id, str):
            raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_SIGNATURE)
        if not key_id or len(key_id) > 200:
            raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_SIGNATURE)

        key = self._resolve_key(key_id)
        try:
            claims = self._decode(token, key)
        except jwt.InvalidSignatureError:
            # A provider may rotate key material while retaining a kid. Refresh once,
            # then fail closed; authentication itself is never retried beyond this.
            key = self._resolve_key(key_id, force_refresh=True)
            try:
                claims = self._decode(token, key)
            except jwt.InvalidSignatureError:
                raise InvalidCredentialsError(
                    AuthenticationFailureReason.INVALID_SIGNATURE
                ) from None
        return self._principal_from_claims(claims, key_id)

    def _decode(self, token: str, key: Any) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[self._configuration.algorithm],
                audience=self._configuration.audience,
                issuer=self._configuration.issuer,
                leeway=self._configuration.clock_skew_seconds,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise InvalidCredentialsError(AuthenticationFailureReason.EXPIRED) from None
        except jwt.InvalidIssuerError:
            raise InvalidCredentialsError(AuthenticationFailureReason.WRONG_ISSUER) from None
        except jwt.InvalidAudienceError:
            raise InvalidCredentialsError(AuthenticationFailureReason.WRONG_AUDIENCE) from None
        except jwt.InvalidSignatureError:
            raise
        except jwt.PyJWTError:
            raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS) from None
        if not isinstance(claims, dict):
            raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS)
        return claims

    def _resolve_key(self, key_id: str, *, force_refresh: bool = False) -> Any:
        with self._lock:
            now = time.monotonic()
            needs_refresh = (
                force_refresh or now >= self._keys_expires_at or key_id not in self._keys
            )
            if needs_refresh:
                self._refresh_keys(force_discovery=force_refresh)
            key = self._keys.get(key_id)
            if key is None and not force_refresh:
                self._refresh_keys(force_discovery=True)
                key = self._keys.get(key_id)
            if key is None:
                raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_SIGNATURE)
            return key

    def _refresh_keys(self, *, force_discovery: bool) -> None:
        try:
            if self._jwks_uri is None or force_discovery:
                self._jwks_uri = self._discover_jwks_uri()
            response = self._http_client.get(self._jwks_uri)
            response.raise_for_status()
            payload = response.json()
            keys = self._parse_keys(payload)
        except InvalidCredentialsError:
            raise
        except (httpx.HTTPError, TypeError, ValueError):
            raise AuthenticationUnavailableError from None
        if not keys:
            raise AuthenticationUnavailableError
        self._keys = keys
        self._keys_expires_at = time.monotonic() + self._configuration.jwks_cache_ttl_seconds

    def _discover_jwks_uri(self) -> str:
        discovery_url = self._configuration.discovery_url or (
            f"{self._configuration.issuer.rstrip('/')}/.well-known/openid-configuration"
        )
        response = self._http_client.get(discovery_url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise AuthenticationUnavailableError
        if payload.get("issuer") != self._configuration.issuer:
            raise AuthenticationUnavailableError
        jwks_uri = payload.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise AuthenticationUnavailableError
        if self._configuration.issuer.startswith("https://") and not jwks_uri.startswith(
            "https://"
        ):
            raise AuthenticationUnavailableError
        return jwks_uri

    def _parse_keys(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise AuthenticationUnavailableError
        keys: dict[str, Any] = {}
        for raw_key in payload["keys"]:
            if not isinstance(raw_key, dict):
                continue
            key_id = raw_key.get("kid")
            if (
                not isinstance(key_id, str)
                or not key_id
                or len(key_id) > 200
                or raw_key.get("kty") != "RSA"
                or raw_key.get("use", "sig") != "sig"
                or raw_key.get("alg", self._configuration.algorithm)
                != self._configuration.algorithm
            ):
                continue
            try:
                keys[key_id] = jwt.PyJWK.from_dict(
                    raw_key, algorithm=self._configuration.algorithm
                ).key
            except (jwt.PyJWTError, ValueError):
                continue
        return keys

    def _principal_from_claims(self, claims: Mapping[str, Any], key_id: str) -> Principal:
        subject = _required_string(claims.get("sub"), max_length=500)
        roles = _string_list(claims.get(self._configuration.roles_claim), max_items=50)
        groups = _string_list(claims.get(self._configuration.groups_claim, []), max_items=100)
        actor_roles = {
            role
            for role in (
                self._configuration.support_role,
                self._configuration.customer_role,
                self._configuration.service_role,
            )
            if role in roles
        }
        if not actor_roles:
            raise InvalidCredentialsError(AuthenticationFailureReason.MISSING_ROLE)
        if len(actor_roles) != 1:
            raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS)

        tenant_value = claims.get(self._configuration.tenant_claim)
        tenant_id = (
            _required_string(tenant_value, max_length=200)
            if self._configuration.require_tenant or tenant_value is not None
            else None
        )
        actor_role = next(iter(actor_roles))
        customer_ids = _customer_ids(
            claims.get(self._configuration.customer_scope_claim),
            required=(
                self._configuration.require_customer_scope
                and actor_role != self._configuration.service_role
            ),
        )
        if actor_role == self._configuration.support_role:
            actor_type = ActorType.SUPPORT_OPERATOR
            customer_id = None
        elif actor_role == self._configuration.customer_role:
            actor_type = ActorType.CUSTOMER
            if len(customer_ids) != 1:
                raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS)
            customer_id = customer_ids[0]
        else:
            actor_type = ActorType.SERVICE
            customer_id = None

        name_value = claims.get(self._configuration.name_claim)
        display_name = (
            name_value.strip()[:200] if isinstance(name_value, str) and name_value.strip() else None
        )
        email_value = claims.get(self._configuration.email_claim)
        email = _required_string(email_value, max_length=320) if email_value is not None else None
        identity_digest = hashlib.sha256(
            f"{self._configuration.issuer}\0{subject}".encode()
        ).hexdigest()
        key_digest = hashlib.sha256(key_id.encode()).hexdigest()
        return Principal(
            actor_id=f"oidc:{identity_digest[:40]}",
            actor_type=actor_type,
            principal_type=PrincipalType.OIDC,
            subject=subject,
            email=email,
            display_name=display_name,
            roles=roles,
            groups=groups,
            tenant_id=tenant_id,
            customer_id=customer_id,
            customer_ids=customer_ids,
            credential_id=f"oidc:{key_digest[:16]}:{identity_digest[:16]}",
        )


def _required_string(value: object, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS)
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS)
    return normalized


def _string_list(value: object, *, max_items: int) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS)
    normalized: list[str] = []
    for item in value:
        text = _required_string(item, max_length=200)
        if text not in normalized:
            normalized.append(text)
    return normalized


def _customer_ids(value: object, *, required: bool) -> list[int]:
    if value is None and not required:
        return []
    raw_values = value if isinstance(value, list) else [value]
    if not raw_values or len(raw_values) > 100:
        raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS)
    customer_ids: list[int] = []
    for item in raw_values:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise InvalidCredentialsError(AuthenticationFailureReason.INVALID_CLAIMS)
        if item not in customer_ids:
            customer_ids.append(item)
    return customer_ids
