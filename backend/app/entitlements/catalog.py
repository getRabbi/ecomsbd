"""Plan catalogue and entitlement keys.

Master spec sections 26 and 43. The rule that shapes this module is: never
scatter ``if plan == "pro"`` through the codebase. Features ask for a named
entitlement; plans are data that maps entitlement keys to values.

Section 51 is the other constraint: **a seller's own historical data is never
locked**. Every plan grants read access to history. What plans gate is volume,
automation, collaboration and advanced analysis.

Prices here are the hypothesis from section 26, still to be validated with real
sellers; they are not a commitment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["PLANS", "UNLIMITED", "Entitlement", "PlanCode", "PlanDefinition", "get_plan"]

#: Sentinel for "no cap". Chosen over ``None`` so comparisons stay numeric.
UNLIMITED = -1


class PlanCode(StrEnum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"


class Entitlement(StrEnum):
    """Entitlement keys (master spec section 43)."""

    ORDERS_MONTHLY_LIMIT = "orders_monthly_limit"
    RISK_CHECKS_DAILY = "risk_checks_daily"
    AUTO_RISK = "auto_risk"
    COURIER_ACCOUNT_LIMIT = "courier_account_limit"
    BULK_BOOKING = "bulk_booking"
    RECONCILIATION = "reconciliation"
    PROFIT_HISTORY_DAYS = "profit_history_days"
    ADVANCED_PROFIT = "advanced_profit"
    SMS_SEGMENTS_MONTHLY = "sms_segments_monthly"
    AI_PARSE_MONTHLY = "ai_parse_monthly"
    TEAM_MEMBER_LIMIT = "team_member_limit"
    WEB_DASHBOARD = "web_dashboard"
    CSV_EXPORT = "csv_export"


@dataclass(frozen=True, slots=True)
class PlanDefinition:
    """One plan: a display name, a price, and its entitlement values."""

    code: PlanCode
    name: str
    price_paisa: int
    entitlements: dict[Entitlement, int | bool]

    def value(self, key: Entitlement) -> int | bool:
        return self.entitlements[key]

    def is_allowed(self, key: Entitlement) -> bool:
        """Whether a boolean entitlement is granted, or a numeric cap is non-zero."""
        value = self.entitlements[key]
        if isinstance(value, bool):
            return value
        return value != 0

    def limit(self, key: Entitlement) -> int:
        value = self.entitlements[key]
        if isinstance(value, bool):
            raise TypeError(f"{key} is a boolean entitlement, not a limit")
        return value


PLANS: dict[PlanCode, PlanDefinition] = {
    PlanCode.FREE: PlanDefinition(
        code=PlanCode.FREE,
        name="Free",
        price_paisa=0,
        entitlements={
            Entitlement.ORDERS_MONTHLY_LIMIT: 20,
            Entitlement.RISK_CHECKS_DAILY: 10,
            Entitlement.AUTO_RISK: False,
            Entitlement.COURIER_ACCOUNT_LIMIT: 1,
            Entitlement.BULK_BOOKING: False,
            # Read-only view of reconciliation results. Section 51: a downgraded
            # seller keeps read access to their own money history.
            Entitlement.RECONCILIATION: False,
            Entitlement.PROFIT_HISTORY_DAYS: 1,
            Entitlement.ADVANCED_PROFIT: False,
            Entitlement.SMS_SEGMENTS_MONTHLY: 0,
            Entitlement.AI_PARSE_MONTHLY: 5,
            Entitlement.TEAM_MEMBER_LIMIT: 1,
            Entitlement.WEB_DASHBOARD: False,
            Entitlement.CSV_EXPORT: False,
        },
    ),
    PlanCode.STARTER: PlanDefinition(
        code=PlanCode.STARTER,
        name="Starter",
        price_paisa=19_900,  # ৳199
        entitlements={
            Entitlement.ORDERS_MONTHLY_LIMIT: UNLIMITED,
            Entitlement.RISK_CHECKS_DAILY: 100,
            Entitlement.AUTO_RISK: True,
            Entitlement.COURIER_ACCOUNT_LIMIT: 3,
            Entitlement.BULK_BOOKING: True,
            Entitlement.RECONCILIATION: True,
            Entitlement.PROFIT_HISTORY_DAYS: UNLIMITED,
            Entitlement.ADVANCED_PROFIT: False,
            Entitlement.SMS_SEGMENTS_MONTHLY: 300,
            Entitlement.AI_PARSE_MONTHLY: 100,
            Entitlement.TEAM_MEMBER_LIMIT: 1,
            Entitlement.WEB_DASHBOARD: False,
            Entitlement.CSV_EXPORT: True,
        },
    ),
    PlanCode.PRO: PlanDefinition(
        code=PlanCode.PRO,
        name="Pro",
        price_paisa=39_900,  # ৳399
        entitlements={
            Entitlement.ORDERS_MONTHLY_LIMIT: UNLIMITED,
            Entitlement.RISK_CHECKS_DAILY: 500,
            Entitlement.AUTO_RISK: True,
            Entitlement.COURIER_ACCOUNT_LIMIT: UNLIMITED,
            Entitlement.BULK_BOOKING: True,
            Entitlement.RECONCILIATION: True,
            Entitlement.PROFIT_HISTORY_DAYS: UNLIMITED,
            Entitlement.ADVANCED_PROFIT: True,
            Entitlement.SMS_SEGMENTS_MONTHLY: 1000,
            Entitlement.AI_PARSE_MONTHLY: 500,
            Entitlement.TEAM_MEMBER_LIMIT: 5,
            Entitlement.WEB_DASHBOARD: True,
            Entitlement.CSV_EXPORT: True,
        },
    ),
}


def get_plan(code: PlanCode | str) -> PlanDefinition:
    """Look up a plan, falling back to Free for an unknown code.

    Falling back rather than raising is deliberate: an unrecognised plan string
    (a rolled-back release, a manual database edit) must degrade to the least
    privilege, never to an exception that blocks the seller's whole app.
    """
    try:
        return PLANS[PlanCode(code)]
    except ValueError:
        return PLANS[PlanCode.FREE]
