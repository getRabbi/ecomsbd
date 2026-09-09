"""Roles and the permission matrix.

Master spec section 88. V1 exposes only ``OWNER`` in the UI, but the schema and
authorisation primitives are complete from the start so shipping the team screen
later is a UI change rather than a rewrite of every endpoint.

Permissions are checked server-side. A mobile client that hides a button has not
enforced anything.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Permission", "TenantRole", "has_permission", "permissions_for"]


class TenantRole(StrEnum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    PACKER = "PACKER"
    ACCOUNTANT = "ACCOUNTANT"
    VIEWER = "VIEWER"


class Permission(StrEnum):
    """Granular capabilities. Endpoints depend on these, never on a role name."""

    ORDER_VIEW = "order.view"
    ORDER_WRITE = "order.write"  # create/edit an unbooked order
    ORDER_BOOK = "order.book"
    ORDER_CANCEL = "order.cancel"

    CUSTOMER_VIEW = "customer.view"
    CUSTOMER_RISK_VIEW = "customer.risk_view"
    CUSTOMER_EXPORT = "customer.export"

    PRODUCT_VIEW = "product.view"
    PRODUCT_WRITE = "product.write"
    INVENTORY_ADJUST = "inventory.adjust"

    MONEY_VIEW = "money.view"
    MONEY_RECONCILE = "money.reconcile"
    MONEY_CORRECT = "money.correct"  # manual financial correction

    COURIER_CREDENTIAL_MANAGE = "courier.credential_manage"

    BILLING_MANAGE = "billing.manage"
    TEAM_MANAGE = "team.manage"
    SETTINGS_MANAGE = "settings.manage"
    DATA_EXPORT = "data.export"


_MATRIX: dict[TenantRole, frozenset[Permission]] = {
    TenantRole.OWNER: frozenset(Permission),
    TenantRole.MANAGER: frozenset(
        {
            Permission.ORDER_VIEW,
            Permission.ORDER_WRITE,
            Permission.ORDER_BOOK,
            Permission.ORDER_CANCEL,
            Permission.CUSTOMER_VIEW,
            Permission.CUSTOMER_RISK_VIEW,
            Permission.PRODUCT_VIEW,
            Permission.PRODUCT_WRITE,
            Permission.INVENTORY_ADJUST,
            Permission.MONEY_VIEW,
        }
    ),
    TenantRole.PACKER: frozenset(
        {
            Permission.ORDER_VIEW,
            Permission.ORDER_WRITE,
            Permission.ORDER_BOOK,
            Permission.CUSTOMER_VIEW,
            Permission.CUSTOMER_RISK_VIEW,
            Permission.PRODUCT_VIEW,
        }
    ),
    TenantRole.ACCOUNTANT: frozenset(
        {
            Permission.ORDER_VIEW,
            Permission.MONEY_VIEW,
            Permission.MONEY_RECONCILE,
            Permission.PRODUCT_VIEW,
            Permission.DATA_EXPORT,
        }
    ),
    TenantRole.VIEWER: frozenset(
        {
            Permission.ORDER_VIEW,
            Permission.CUSTOMER_VIEW,
            Permission.PRODUCT_VIEW,
        }
    ),
}


def permissions_for(role: TenantRole | str) -> frozenset[Permission]:
    try:
        return _MATRIX[TenantRole(role)]
    except ValueError:
        return frozenset()


def has_permission(role: TenantRole | str, permission: Permission) -> bool:
    return permission in permissions_for(role)
