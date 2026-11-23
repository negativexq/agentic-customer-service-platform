from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ActorType(StrEnum):
    CUSTOMER = "customer"
    SUPPORT_OPERATOR = "support_operator"
    SERVICE = "service"


class CredentialScheme(StrEnum):
    BEARER = "bearer"


class PrincipalType(StrEnum):
    LOCAL_DEMO = "local_demo"
    STATIC = "static"
    OIDC = "oidc"
    INTERNAL = "internal"


class AuthenticationCredentials(BaseModel):
    """Secret-bearing input passed only across the authentication boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scheme: CredentialScheme
    secret: SecretStr


class Principal(BaseModel):
    """Authenticated actor identity safe to pass through application layers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: str = Field(min_length=1, max_length=200)
    actor_type: ActorType
    principal_type: PrincipalType = PrincipalType.STATIC
    subject: str | None = Field(
        default=None, min_length=1, max_length=500, repr=False, exclude=True
    )
    email: str | None = Field(default=None, min_length=3, max_length=320, repr=False, exclude=True)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    roles: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=50
    )
    groups: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=100
    )
    tenant_id: str | None = Field(default=None, min_length=1, max_length=200)
    customer_id: int | None = Field(default=None, gt=0)
    customer_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=100)
    credential_id: str | None = Field(default=None, min_length=1, max_length=200)


class CustomerScope(BaseModel):
    """Server-resolved customer boundary for a single request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_id: int = Field(gt=0)
    principal: Principal
