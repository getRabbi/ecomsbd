"""Platform admin authentication.

Master spec section 44: *"Admin access must be independently authorized."*

An admin presents ``X-Admin-Token``. That token is never a seller access token
and never grants seller access: the two identities do not overlap, so a stolen
app session is worth nothing here and a stolen admin token cannot post an order.

Two sources, in this order:

1.  **A ``platform_admins`` row**, matched by keyed hash. This is the normal
    path: tokens are issued individually, carry a role, and can be revoked.
2.  **A bootstrap token from configuration**, for the first operator on a fresh
    deployment, before any row exists. It maps to a ``SUPERADMIN`` labelled
    ``bootstrap:<n>`` so every audit entry says plainly that the shared
    credential was used.

With neither configured — which is the shipped default — there is no admin
access at all. That is the correct posture: an ops console that is reachable
before anyone has provisioned access is a vulnerability, not a convenience.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.models import (
    AdminPermission,
    PlatformAdmin,
    PlatformAdminRole,
    admin_permissions_for,
)
from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.errors import AuthenticationError, ErrorCode, ForbiddenError
from app.core.security import SecretHasher

__all__ = ["ADMIN_TOKEN_HEADER", "AdminPrincipal", "authenticate_admin"]

ADMIN_TOKEN_HEADER = "x-admin-token"


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    """An authenticated operator."""

    admin_id: uuid.UUID | None
    label: str
    role: PlatformAdminRole
    #: True when authenticated from a shared configuration token rather than a
    #: provisioned row. Surfaced in audit context so a review can tell them apart.
    is_bootstrap: bool = False

    @property
    def permissions(self) -> frozenset[AdminPermission]:
        return admin_permissions_for(self.role)

    def can(self, permission: AdminPermission) -> bool:
        return permission in self.permissions

    def require(self, permission: AdminPermission) -> None:
        if not self.can(permission):
            raise ForbiddenError(
                f"The {self.role} admin role does not allow {permission}",
                details={"required": str(permission), "role": str(self.role)},
            )


async def authenticate_admin(
    token: str | None,
    *,
    session: AsyncSession,
    settings: Settings,
    hasher: SecretHasher,
) -> AdminPrincipal:
    """Resolve an admin from a presented token, or refuse.

    Failures are audited before they are raised, because a stream of rejected
    admin tokens is exactly the signal an operator wants to see.
    """
    if not token:
        raise AuthenticationError(
            "Platform administration requires an admin token",
            code=ErrorCode.UNAUTHENTICATED,
        )

    for index, configured in enumerate(settings.admin_api_tokens):
        # Constant-time, and over every configured token rather than breaking
        # on the first match, so response timing does not reveal which one hit.
        if hmac.compare_digest(token, configured):
            return AdminPrincipal(
                admin_id=None,
                label=f"bootstrap:{index}",
                role=PlatformAdminRole.SUPERADMIN,
                is_bootstrap=True,
            )

    row = (
        await session.execute(
            sa.select(PlatformAdmin).where(PlatformAdmin.token_hash == hasher.token_hash(token))
        )
    ).scalar_one_or_none()

    if row is None or not row.is_usable():
        await record_audit(
            session,
            AuditAction.ADMIN_ACCESS_DENIED,
            entity_type="platform_admin",
            entity_id=row.id if row else None,
            context={"reason": "revoked_or_expired" if row else "unknown_token"},
        )
        # Committed before the refusal is raised. The session rolls back on an
        # exception, and a stream of rejected admin tokens is exactly the
        # signal that must not be the thing that disappears.
        await session.commit()
        raise AuthenticationError("This admin token is not valid", code=ErrorCode.INVALID_TOKEN)

    row.last_used_at = utc_now()
    return AdminPrincipal(
        admin_id=row.id,
        label=row.label,
        role=PlatformAdminRole(row.role),
    )
