"""Every smart-alert threshold, cooldown and audience, in one place.

Nothing in :mod:`app.notifications.smart` hard-codes a number: a threshold that
lives next to the query that uses it is a threshold nobody can find when a
seller asks why they were (or were not) told something. Values here are
conservative on purpose — an alert that fires on a normal week teaches the
seller to ignore the one that matters.

Thresholds are module attributes read at call time, so a deployment (or a
test) can change one without touching the detector.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Final

from app.consignments.models import ConsignmentStatus
from app.notifications.models import NotificationCategory, NotificationKind, Severity
from app.reconciliation.models import CaseKind
from app.tenants.roles import Permission

__all__ = [
    "ALERT_RULES",
    "CATEGORY_AUDIENCE",
    "DISCREPANCY_CASE_KINDS",
    "IMPORT_AUDIENCE",
    "IMPORT_THRESHOLDS",
    "RTO_THRESHOLDS",
    "STUCK_AFTER",
    "AlertRule",
    "ImportThresholds",
    "RtoThresholds",
    "audience_for",
    "category_for",
    "rule_for",
]


@dataclass(frozen=True, slots=True)
class AlertRule:
    """How one kind of alert behaves once its condition is true."""

    category: NotificationCategory
    severity: Severity
    #: An unchanged condition is not announced again inside this window.
    cooldown: timedelta
    #: The permission a member needs to receive it. ``None`` inherits the
    #: category's audience.
    audience: Permission | None = None
    #: Material worsening: the tracked figure must grow by at least this
    #: fraction *and* by at least ``worsen_min_delta`` before a fresh alert is
    #: raised inside the cooldown.
    worsen_ratio: float = 0.5
    worsen_min_delta: int = 1
    #: …and never sooner than this after the previous one, however fast it
    #: grows. Two pushes about the same thing in one morning is noise.
    worsen_min_gap: timedelta = timedelta(hours=20)
    #: Where tapping it goes when it is not about one record.
    route: str = "notifications"


#: Who receives each category. Backend-decided, by permission rather than by
#: role name, so a role's matrix change carries through without edits here.
CATEGORY_AUDIENCE: Final = MappingProxyType(
    {
        # OWNER, MANAGER, FINANCE.
        NotificationCategory.MONEY: Permission.MONEY_VIEW,
        NotificationCategory.RECONCILIATION: Permission.MONEY_VIEW,
        # OWNER, MANAGER, ORDER_OPERATOR — the people who book parcels.
        NotificationCategory.COURIER: Permission.ORDER_BOOK,
        NotificationCategory.RETURNS: Permission.CUSTOMER_RISK_VIEW,
        # OWNER, MANAGER.
        NotificationCategory.INVENTORY: Permission.INVENTORY_ADJUST,
        # Addressed to the uploader; this is the fallback when nobody is known.
        NotificationCategory.IMPORTS: Permission.ORDER_WRITE,
        NotificationCategory.CRM: Permission.ORDER_WRITE,
        NotificationCategory.SUMMARY: Permission.MONEY_VIEW,
    }
)

_DAY = timedelta(days=1)

ALERT_RULES: Final = MappingProxyType(
    {
        NotificationKind.FOLLOW_UP_DUE: AlertRule(
            category=NotificationCategory.CRM,
            severity=Severity.ACTION,
            cooldown=3 * _DAY,
            route="customers",
        ),
        NotificationKind.PAYOUT_OVERDUE: AlertRule(
            category=NotificationCategory.MONEY,
            severity=Severity.CRITICAL,
            cooldown=7 * _DAY,
            # Money: half as much again, and at least ৳1,000 more.
            worsen_min_delta=100_000,
            route="receivables",
        ),
        NotificationKind.RECONCILIATION_DISCREPANCY: AlertRule(
            category=NotificationCategory.RECONCILIATION,
            severity=Severity.WARNING,
            cooldown=7 * _DAY,
            worsen_min_delta=3,
            route="reconciliation",
        ),
        NotificationKind.COURIER_STATUS_STUCK: AlertRule(
            category=NotificationCategory.COURIER,
            severity=Severity.WARNING,
            cooldown=3 * _DAY,
            worsen_min_delta=3,
            route="orders",
        ),
        NotificationKind.HIGH_RTO: AlertRule(
            category=NotificationCategory.RETURNS,
            severity=Severity.WARNING,
            cooldown=14 * _DAY,
            # Basis points: a rate five points above the one last reported.
            worsen_ratio=0.0,
            worsen_min_delta=500,
            worsen_min_gap=7 * _DAY,
            route="returns",
        ),
        NotificationKind.LOW_STOCK: AlertRule(
            category=NotificationCategory.INVENTORY,
            severity=Severity.ACTION,
            cooldown=3 * _DAY,
            worsen_min_delta=2,
            route="products",
        ),
        NotificationKind.NEGATIVE_MARGIN: AlertRule(
            category=NotificationCategory.MONEY,
            severity=Severity.WARNING,
            cooldown=7 * _DAY,
            worsen_min_delta=3,
            route="profit",
        ),
        NotificationKind.IMPORT_FAILURE: AlertRule(
            category=NotificationCategory.IMPORTS,
            severity=Severity.ACTION,
            # One import is one event; it is never re-announced.
            cooldown=timedelta(days=3650),
            worsen_min_delta=10**9,
            route="imports",
        ),
        NotificationKind.COURIER_ACCOUNT_PROBLEM: AlertRule(
            category=NotificationCategory.COURIER,
            severity=Severity.CRITICAL,
            cooldown=3 * _DAY,
            # Only someone who may enter credentials can fix it.
            audience=Permission.COURIER_CREDENTIAL_MANAGE,
            worsen_min_delta=10**9,
            route="courier_accounts",
        ),
        NotificationKind.RETURNED_NOT_RESTOCKED: AlertRule(
            category=NotificationCategory.INVENTORY,
            severity=Severity.ACTION,
            cooldown=7 * _DAY,
            worsen_min_delta=3,
            route="reconciliation",
        ),
    }
)

#: Pre-V2.2 kinds, for rows raised before categories existed.
_LEGACY_CATEGORY: Final = MappingProxyType(
    {
        NotificationKind.DELIVERED_BUT_UNPAID: NotificationCategory.MONEY,
        NotificationKind.UNDERPAID: NotificationCategory.RECONCILIATION,
        NotificationKind.STALE_IN_TRANSIT: NotificationCategory.COURIER,
        NotificationKind.RETURN_SPIKE: NotificationCategory.RETURNS,
        NotificationKind.WEEKLY_SUMMARY: NotificationCategory.SUMMARY,
        NotificationKind.BUNDLE: NotificationCategory.SUMMARY,
    }
)


def rule_for(kind: NotificationKind) -> AlertRule:
    return ALERT_RULES[kind]


def category_for(kind: NotificationKind | str) -> NotificationCategory:
    kind = NotificationKind(str(kind))
    if kind in ALERT_RULES:
        return ALERT_RULES[kind].category
    return _LEGACY_CATEGORY.get(kind, NotificationCategory.SUMMARY)


def audience_for(kind: NotificationKind | str) -> Permission:
    kind = NotificationKind(str(kind))
    rule = ALERT_RULES.get(kind)
    if rule is not None and rule.audience is not None:
        return rule.audience
    return CATEGORY_AUDIENCE[category_for(kind)]


# --------------------------------------------------------------------------- #
# Detector thresholds
# --------------------------------------------------------------------------- #


#: How long a parcel may sit in one non-final courier status, with no movement
#: at all, before it is worth a seller's call. Measured from the last status
#: change, not from booking, so a parcel that is moving is never "stuck".
#: Deliberately above a normal outside-Dhaka delivery (3–5 days).
STUCK_AFTER: Final = MappingProxyType(
    {
        str(ConsignmentStatus.BOOKED): timedelta(days=5),
        str(ConsignmentStatus.PICKED_UP): timedelta(days=7),
        str(ConsignmentStatus.IN_TRANSIT): timedelta(days=10),
        str(ConsignmentStatus.OUT_FOR_DELIVERY): timedelta(days=5),
        str(ConsignmentStatus.RETURN_REQUESTED): timedelta(days=10),
        str(ConsignmentStatus.RETURNING): timedelta(days=14),
    }
)


@dataclass(frozen=True, slots=True)
class RtoThresholds:
    #: The RTO Intelligence screen's own window.
    window_days: int = 30
    #: Completed parcels (delivered + partial + RTO) needed before a rate is
    #: alerted on at all. Twice the screen's "limited data" line: a warning
    #: interrupts, a table row does not.
    min_completed: int = 20
    #: A rate at or above this is reported whatever the history.
    high_rate_bps: int = 3_000
    #: …or a rise of this much against the previous window of the same length,
    #: when that window had the minimum sample too.
    rise_bps: int = 1_000


RTO_THRESHOLDS = RtoThresholds()


@dataclass(frozen=True, slots=True)
class ImportThresholds:
    #: Rejected rows that are worth telling someone about: this many…
    min_rejected_rows: int = 5
    #: …or this share of the file (basis points), whichever comes first.
    min_rejected_share_bps: int = 1_000
    #: Imports finished this recently are checked by the daily scan, so one
    #: whose event was missed is still reported.
    lookback: timedelta = timedelta(days=2)


IMPORT_THRESHOLDS = ImportThresholds()

#: Who hears about an import's outcome when its uploader is unknown.
IMPORT_AUDIENCE: Final = MappingProxyType(
    {
        "ORDERS": Permission.ORDER_WRITE,
        "PRODUCTS": Permission.PRODUCT_WRITE,
        "PAYOUT_STATEMENT": Permission.MONEY_RECONCILE,
    }
)

#: Reconciliation V2 cases that are a settlement discrepancy. Delivered-but-
#: unpaid is left to PAYOUT_OVERDUE (the Receivables rule) and a stale parcel to
#: COURIER_STATUS_STUCK, so the same money is never announced twice.
DISCREPANCY_CASE_KINDS: Final = frozenset(
    {
        CaseKind.MISSING_COD,
        CaseKind.UNDERPAID,
        CaseKind.OVERPAID,
        CaseKind.CHARGE_MISMATCH,
        CaseKind.RETURN_CHARGE_MISMATCH,
        CaseKind.UNKNOWN_DEDUCTION,
        CaseKind.DUPLICATE_PAYOUT_LINE,
        CaseKind.UNMAPPABLE_PAYOUT,
    }
)

#: Delivered parcels settled this recently are checked for a negative margin.
NEGATIVE_MARGIN_WINDOW_DAYS = 7

#: Names/ids carried in a summary alert's parameters. The count is the fact;
#: the list is a convenience and is capped so a payload stays small.
MAX_LISTED = 5
