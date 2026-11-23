"""One authorization primitive for operator customer scope, shared by every operator route."""

from app.auth.models import Principal, PrincipalType
from app.core.config import get_settings


def operator_customer_filter(principal: Principal) -> list[int] | None:
    """Return the customer IDs an operator may read, or None for the whole tenant.

    OIDC operators are row-scoped to their ``customer_ids`` claim unless they hold the optional
    tenant-wide support role. Tenant-wide access never crosses tenants: callers still filter
    by ``principal.tenant_id``.
    """

    if principal.principal_type != PrincipalType.OIDC:
        return None
    tenant_wide_role = get_settings().oidc_support_tenant_wide_role
    if tenant_wide_role is not None and tenant_wide_role in principal.roles:
        return None
    return principal.customer_ids


def operator_may_access(principal: Principal, customer_id: int) -> bool:
    allowed = operator_customer_filter(principal)
    return allowed is None or customer_id in allowed
