"""Who gets called: the confirmation policy (V4 WP2.2, rule L9).

A pure, deterministic function of a few counts and the shop's own thresholds.
No score, no model: every decision comes with a reason code, and the facts it
read are stored next to it, so the seller can check the arithmetic.

The rules are tried in a fixed order and the first match wins. The order is
not configurable; only the thresholds are (``shop_delivery_rules``):

1. the seller blocked the customer          → ``SELLER_REVIEW`` / ``CUSTOMER_BLOCKED``
2. external success < ``risk_low_bps`` over ≥ 5 parcels, or own-shop band
   ``HIGH``                                    → ``SELLER_REVIEW`` / ``HIGH_RISK``
3. COD > ``max_auto_cod_paisa``              → ``CALL`` / ``HIGH_VALUE``
4. ≥ ``trusted_repeat_min`` own-shop deliveries and band ``LOW``
                                              → ``AUTO_CONFIRM`` / ``TRUSTED_REPEAT``
5. external ≥ ``trusted_network_bps`` over ≥ 5 parcels and no own-shop return
                                              → ``AUTO_CONFIRM`` / ``TRUSTED_NETWORK``
6. anything else                              → ``CALL`` / ``VERIFY_NEW``

Overrides: ``call_nobody`` turns rules 3–6 into ``SELLER_REVIEW`` /
``SHOP_MANUAL``; ``call_everyone`` turns rules 4–5 into ``CALL`` /
``CALL_EVERYONE``. Rules 1 and 2 always keep their own answer.

External data is used only when a provider answered: without it, the external
half of rule 2 and all of rule 5 are skipped. Rule 5 also needs the answer to
be fresh (inside the provider's cache window): an old "trusted" answer never
auto-confirms, while an old "risky" one still sends the order to the seller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from app.confirmations.models import ConfirmationDecision
from app.customers.risk import RiskState

__all__ = [
    "EXTERNAL_MIN_PARCELS",
    "ExternalHistory",
    "PolicyFacts",
    "PolicyRules",
    "ReasonCode",
    "decide",
]

D = ConfirmationDecision

#: External history counts only over at least this many parcels (spec; fixed).
EXTERNAL_MIN_PARCELS = 5


class ReasonCode(StrEnum):
    """Why the policy decided what it did. Stored on the confirmation row."""

    CUSTOMER_BLOCKED = "CUSTOMER_BLOCKED"
    HIGH_RISK = "HIGH_RISK"
    HIGH_VALUE = "HIGH_VALUE"
    TRUSTED_REPEAT = "TRUSTED_REPEAT"
    TRUSTED_NETWORK = "TRUSTED_NETWORK"
    VERIFY_NEW = "VERIFY_NEW"
    #: The shop's ``call_nobody`` switch: the seller confirms every order.
    SHOP_MANUAL = "SHOP_MANUAL"
    #: The shop's ``call_everyone`` switch: even a trusted customer is called.
    CALL_EVERYONE = "CALL_EVERYONE"


@dataclass(frozen=True, slots=True)
class ExternalHistory:
    """What an external provider last answered about the customer's parcels."""

    #: Parcels the provider counted (all couriers); 0 when it had none.
    parcels: int
    #: Delivered share in basis points; ``None`` when ``parcels`` is 0.
    success_bps: int | None
    #: Still inside the provider's cache window.
    fresh: bool

    def counts(self) -> bool:
        return self.parcels >= EXTERNAL_MIN_PARCELS and self.success_bps is not None


@dataclass(frozen=True, slots=True)
class PolicyFacts:
    """Everything the policy reads about one order. Counts and codes only."""

    customer_blocked: bool
    cod_paisa: int
    #: The shop's own band for this customer (:func:`app.customers.risk.assess`).
    own_band: RiskState
    own_delivered: int
    own_returned: int
    #: ``None`` when no external provider answered (unavailable).
    external: ExternalHistory | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["own_band"] = str(self.own_band)
        return data


@dataclass(frozen=True, slots=True)
class PolicyRules:
    """The shop's thresholds and switches (``shop_delivery_rules``)."""

    max_auto_cod_paisa: int = 500_000
    risk_low_bps: int = 5000
    trusted_network_bps: int = 8500
    trusted_repeat_min: int = 2
    call_everyone: bool = False
    call_nobody: bool = False

    @classmethod
    def from_values(cls, values: Any) -> PolicyRules:
        """Read the six policy keys from the effective delivery rules."""
        return cls(
            max_auto_cod_paisa=int(values["max_auto_cod_paisa"]),
            risk_low_bps=int(values["risk_low_bps"]),
            trusted_network_bps=int(values["trusted_network_bps"]),
            trusted_repeat_min=int(values["trusted_repeat_min"]),
            call_everyone=bool(values["call_everyone"]),
            call_nobody=bool(values["call_nobody"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _matched_rule(facts: PolicyFacts, rules: PolicyRules) -> int:
    """The number (1–6) of the first rule that matches."""
    external = facts.external
    if facts.customer_blocked:
        return 1
    external_risky = (
        external is not None
        and external.counts()
        and external.success_bps is not None
        and external.success_bps < rules.risk_low_bps
    )
    if external_risky or facts.own_band is RiskState.HIGH:
        return 2
    if facts.cod_paisa > rules.max_auto_cod_paisa:
        return 3
    if facts.own_delivered >= rules.trusted_repeat_min and facts.own_band is RiskState.LOW:
        return 4
    if (
        external is not None
        and external.fresh
        and external.counts()
        and external.success_bps is not None
        and external.success_bps >= rules.trusted_network_bps
        and facts.own_returned == 0
    ):
        return 5
    return 6


_BY_RULE: dict[int, tuple[ConfirmationDecision, ReasonCode]] = {
    1: (D.SELLER_REVIEW, ReasonCode.CUSTOMER_BLOCKED),
    2: (D.SELLER_REVIEW, ReasonCode.HIGH_RISK),
    3: (D.CALL, ReasonCode.HIGH_VALUE),
    4: (D.AUTO_CONFIRM, ReasonCode.TRUSTED_REPEAT),
    5: (D.AUTO_CONFIRM, ReasonCode.TRUSTED_NETWORK),
    6: (D.CALL, ReasonCode.VERIFY_NEW),
}


def decide(facts: PolicyFacts, rules: PolicyRules) -> tuple[ConfirmationDecision, ReasonCode]:
    """The decision and its reason code for one order. Pure; first match wins."""
    rule = _matched_rule(facts, rules)
    if rules.call_nobody and rule >= 3:
        return D.SELLER_REVIEW, ReasonCode.SHOP_MANUAL
    if rules.call_everyone and rule in (4, 5):
        return D.CALL, ReasonCode.CALL_EVERYONE
    return _BY_RULE[rule]
