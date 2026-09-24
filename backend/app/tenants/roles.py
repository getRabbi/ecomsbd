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
    """The five roles a shop can assign.

    ``PACKER`` and ``ACCOUNTANT`` were V1's names for the two middle roles.
    They are kept as **aliases**, not as separate members: ``TenantRole.PACKER
    is TenantRole.ORDER_OPERATOR`` is true, so existing call sites keep
    compiling and mean exactly what they meant before. :meth:`_missing_` maps
    the old strings too, so a row written before the rename — or a token issued
    before it — still resolves instead of silently losing its permissions.
    """

    OWNER = "OWNER"
    MANAGER = "MANAGER"
    #: Orders, customers, products and courier booking. No financial admin and
    #: no access to courier credentials.
    ORDER_OPERATOR = "ORDER_OPERATOR"
    #: Money, COD, payouts, reconciliation and expenses. Little operational
    #: mutation.
    FINANCE = "FINANCE"
    VIEWER = "VIEWER"

    # --- V1 names, same members -------------------------------------------
    PACKER = "ORDER_OPERATOR"
    ACCOUNTANT = "FINANCE"

    @classmethod
    def _missing_(cls, value: object) -> TenantRole | None:
        """Resolve a role stored or sent under its V1 name.

        A membership row written before the rename, or a JWT minted before it,
        must keep working. Returning ``None`` here would make
        :func:`permissions_for` hand back an empty set, which reads as "this
        person may do nothing" — a silent, total lockout rather than an error
        anyone would notice.
        """
        if isinstance(value, str):
            return _LEGACY_ROLE_NAMES.get(value.strip().upper())
        return None


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
    #: Create, edit, publish and switch off automation workflows (V3.4).
    AUTOMATION_MANAGE = "automation.manage"
    #: V3.5: suppliers, purchase orders, over-receipt approval, locations.
    PROCUREMENT_MANAGE = "procurement.manage"
    #: V3.5: record payments to suppliers (not COD, not payouts).
    PAYABLE_MANAGE = "payable.manage"
    DATA_EXPORT = "data.export"


#: V1 role names -> the members they became. Read by ``TenantRole._missing_``.
_LEGACY_ROLE_NAMES: dict[str, TenantRole] = {
    "PACKER": TenantRole.ORDER_OPERATOR,
    "ACCOUNTANT": TenantRole.FINANCE,
}


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
            Permission.AUTOMATION_MANAGE,
            Permission.PROCUREMENT_MANAGE,
        }
    ),
    TenantRole.ORDER_OPERATOR: frozenset(
        {
            Permission.ORDER_VIEW,
            Permission.ORDER_WRITE,
            Permission.ORDER_BOOK,
            Permission.CUSTOMER_VIEW,
            Permission.CUSTOMER_RISK_VIEW,
            Permission.PRODUCT_VIEW,
        }
    ),
    TenantRole.FINANCE: frozenset(
        {
            Permission.ORDER_VIEW,
            Permission.MONEY_VIEW,
            Permission.MONEY_RECONCILE,
            Permission.PRODUCT_VIEW,
            Permission.DATA_EXPORT,
            Permission.PAYABLE_MANAGE,
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
    """Everything a role may do. An unknown role gets nothing.

    An empty set is the safe answer for a value nobody recognises: a role name
    this build has never seen must not be treated as more privileged than the
    ones it has.
    """
    try:
        return _MATRIX[TenantRole(role)]
    except ValueError:
        return frozenset()


def has_permission(role: TenantRole | str, permission: Permission) -> bool:
    return permission in permissions_for(role)
