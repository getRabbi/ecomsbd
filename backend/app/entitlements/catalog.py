"""Plan catalogue and entitlement keys.

Master spec sections 26 and 43. The rule that shapes this module is: never
scatter ``if plan == "pro"`` through the codebase. Features ask for a named
entitlement; plans are data that maps entitlement keys to values.

Section 51 is the other constraint: **a seller's own historical data is never
locked**. Every plan grants read access to history. What plans gate is volume,
automation, collaboration and advanced analysis.

Prices here are the hypothesis from section 26, still to be validated with real
sellers; they are not a commitment. Because they are product-validation data
rather than a technical constant, both the displayed price and the entitlement
values are **overridable from configuration** — see :func:`plan_catalog`. Plan
*codes* are not overridable: they are written into subscriptions, usage counters
and provider product mappings, so renaming one would orphan existing rows.

A price is never copied into a business table. What a seller was actually
charged is a snapshot on the billing transaction; this module only says what a
plan costs today.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

__all__ = [
    "PLANS",
    "UNLIMITED",
    "Entitlement",
    "PlanCode",
    "PlanDefinition",
    "get_plan",
    "plan_catalog",
]

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


def _apply_overrides(
    plans: dict[PlanCode, PlanDefinition],
    prices: Mapping[str, Any] | None,
    entitlements: Mapping[str, Any] | None,
) -> dict[PlanCode, PlanDefinition]:
    """Apply configured price and entitlement overrides to a catalogue copy.

    Unknown plan codes and unknown entitlement keys are ignored rather than
    raising. A typo in an operator's environment variable must not take the API
    down; it shows up as "the price did not change", which is visible and
    harmless, instead of a boot failure at 2am.
    """
    result = dict(plans)

    for code_str, price in (prices or {}).items():
        try:
            code = PlanCode(code_str)
        except ValueError:
            continue
        if isinstance(price, int) and price >= 0:
            result[code] = replace(result[code], price_paisa=price)

    for code_str, values in (entitlements or {}).items():
        try:
            code = PlanCode(code_str)
        except ValueError:
            continue
        if not isinstance(values, Mapping):
            continue
        merged = dict(result[code].entitlements)
        for key_str, value in values.items():
            try:
                key = Entitlement(key_str)
            except ValueError:
                continue
            current = merged[key]
            # Refuse a type change: flipping a numeric cap to a boolean would
            # make plan.limit() raise deep inside a request.
            if (isinstance(current, bool) and isinstance(value, bool)) or (
                not isinstance(current, bool) and isinstance(value, int)
            ):
                merged[key] = value
        result[code] = replace(result[code], entitlements=merged)

    return result


def plan_catalog(
    *,
    price_overrides: Mapping[str, Any] | None = None,
    entitlement_overrides: Mapping[str, Any] | None = None,
) -> dict[PlanCode, PlanDefinition]:
    """The plan catalogue with configuration applied.

    Called with no arguments this reads the process settings, so ordinary code
    can simply ask for the catalogue. Tests pass overrides explicitly.
    """
    if price_overrides is None and entitlement_overrides is None:
        from app.core.config import get_settings

        settings = get_settings()
        price_overrides = settings.plan_price_overrides
        entitlement_overrides = settings.plan_entitlement_overrides

    return _apply_overrides(PLANS, price_overrides, entitlement_overrides)


def get_plan(code: PlanCode | str) -> PlanDefinition:
    """Look up a plan, falling back to Free for an unknown code.

    Falling back rather than raising is deliberate: an unrecognised plan string
    (a rolled-back release, a manual database edit) must degrade to the least
    privilege, never to an exception that blocks the seller's whole app.
    """
    catalog = plan_catalog()
    try:
        return catalog[PlanCode(code)]
    except ValueError:
        return catalog[PlanCode.FREE]
