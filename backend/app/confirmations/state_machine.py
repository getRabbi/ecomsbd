"""The confirmation state machine (V4 WP2.1).

Every legal move is one row of :data:`TRANSITIONS`: a ``(from, to)`` pair and
the causes that may make it. Anything not in the table is illegal, so a new
path has to be added here on purpose rather than appearing by accident in a
handler. A terminal cause also fixes the ``method`` recorded on the row, so
"confirmed by the customer on a call" can never be written by the seller's
button.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final

from app.confirmations.models import (
    OPEN_STATES,
    ConfirmationMethod,
    ConfirmationState,
)
from app.core.errors import ConflictError, ValidationError

__all__ = [
    "SELLER_METHODS",
    "TRANSITIONS",
    "Cause",
    "ConfirmationStateMachine",
]

S = ConfirmationState
M = ConfirmationMethod


class Cause(StrEnum):
    """Why a confirmation moved."""

    # The policy (WP2.2) on a PENDING row.
    AUTO_TRUSTED = "AUTO_TRUSTED"
    DECIDED_CALL = "DECIDED_CALL"
    DECIDED_REVIEW = "DECIDED_REVIEW"
    # The call queue (Phase 3) and the manual queue (WP2.3).
    DIALING = "DIALING"
    VOICE_UNAVAILABLE = "VOICE_UNAVAILABLE"
    #: The customer's number already had its calls for the day (WP3.2).
    CALL_LIMIT = "CALL_LIMIT"
    #: The shop has no voice call credit left (WP3.4).
    NO_VOICE_CREDIT = "NO_VOICE_CREDIT"
    # What a call returned.
    DTMF_CONFIRM = "DTMF_CONFIRM"
    DTMF_CANCEL = "DTMF_CANCEL"
    DTMF_CALLBACK = "DTMF_CALLBACK"
    CALL_UNKNOWN = "CALL_UNKNOWN"
    #: The provider certainly did not place the call (WP3.2).
    CALL_FAILED = "CALL_FAILED"
    CALL_NO_ANSWER = "CALL_NO_ANSWER"
    # After no answer.
    RETRY = "RETRY"
    LINK_SENT = "LINK_SENT"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"
    # The SMS link (WP2.3).
    LINK_CONFIRM = "LINK_CONFIRM"
    LINK_CANCEL = "LINK_CANCEL"
    LINK_EXPIRED = "LINK_EXPIRED"
    # The seller, from the review queue.
    SELLER_CONFIRM = "SELLER_CONFIRM"
    SELLER_CANCEL = "SELLER_CANCEL"
    # The order itself.
    ORDER_EDITED = "ORDER_EDITED"
    ORDER_CANCELLED = "ORDER_CANCELLED"


def _table() -> dict[tuple[ConfirmationState, ConfirmationState], frozenset[Cause]]:
    table: dict[tuple[ConfirmationState, ConfirmationState], set[Cause]] = {
        (S.PENDING, S.CONFIRMED): {Cause.AUTO_TRUSTED},
        (S.PENDING, S.CALL_QUEUED): {Cause.DECIDED_CALL},
        (S.PENDING, S.SELLER_REVIEW): {Cause.DECIDED_REVIEW},
        (S.CALL_QUEUED, S.CALLING): {Cause.DIALING},
        (S.CALL_QUEUED, S.SELLER_REVIEW): {
            Cause.VOICE_UNAVAILABLE,
            Cause.CALL_LIMIT,
            Cause.NO_VOICE_CREDIT,
        },
        (S.CALLING, S.CONFIRMED): {Cause.DTMF_CONFIRM},
        (S.CALLING, S.CANCELLED): {Cause.DTMF_CANCEL},
        (S.CALLING, S.SELLER_REVIEW): {Cause.DTMF_CALLBACK, Cause.CALL_UNKNOWN, Cause.CALL_FAILED},
        (S.CALLING, S.NO_ANSWER): {Cause.CALL_NO_ANSWER},
        (S.NO_ANSWER, S.CALL_QUEUED): {Cause.RETRY},
        (S.NO_ANSWER, S.SMS_LINK_SENT): {Cause.LINK_SENT},
        (S.NO_ANSWER, S.SELLER_REVIEW): {Cause.ATTEMPTS_EXHAUSTED},
        (S.SMS_LINK_SENT, S.CONFIRMED): {Cause.LINK_CONFIRM},
        (S.SMS_LINK_SENT, S.CANCELLED): {Cause.LINK_CANCEL},
        (S.SMS_LINK_SENT, S.SELLER_REVIEW): {Cause.LINK_EXPIRED},
        (S.SELLER_REVIEW, S.CONFIRMED): {Cause.SELLER_CONFIRM},
        (S.SELLER_REVIEW, S.CANCELLED): {Cause.SELLER_CANCEL},
    }
    for state in OPEN_STATES:
        table.setdefault((state, S.SUPERSEDED), set()).add(Cause.ORDER_EDITED)
        table.setdefault((state, S.CANCELLED), set()).add(Cause.ORDER_CANCELLED)
    return {pair: frozenset(causes) for pair, causes in table.items()}


#: The whole machine. Read-only.
TRANSITIONS: Final[Mapping[tuple[ConfirmationState, ConfirmationState], frozenset[Cause]]] = (
    MappingProxyType(_table())
)

#: The method a terminal cause records. Seller causes take the seller's own
#: method (see :data:`SELLER_METHODS`); the order ending it records none.
_METHOD_FOR: Final[Mapping[Cause, ConfirmationMethod]] = MappingProxyType(
    {
        Cause.AUTO_TRUSTED: M.AUTO_TRUSTED,
        Cause.DTMF_CONFIRM: M.IVR,
        Cause.DTMF_CANCEL: M.IVR,
        Cause.LINK_CONFIRM: M.SMS_LINK,
        Cause.LINK_CANCEL: M.SMS_LINK,
    }
)

#: How a seller may say they confirmed: from the review queue, after their own
#: call, or because the customer already confirmed in the chat.
SELLER_METHODS: Final = frozenset({M.SELLER, M.MANUAL_CALL, M.SELLER_CHAT})
_SELLER_CAUSES: Final = frozenset({Cause.SELLER_CONFIRM, Cause.SELLER_CANCEL})


class ConfirmationStateMachine:
    """Checks a move against :data:`TRANSITIONS`. Holds no state of its own."""

    transitions: ClassVar[
        Mapping[tuple[ConfirmationState, ConfirmationState], frozenset[Cause]]
    ] = TRANSITIONS

    @classmethod
    def is_allowed(
        cls, current: ConfirmationState, target: ConfirmationState, cause: Cause
    ) -> bool:
        return cause in cls.transitions.get((current, target), frozenset())

    @classmethod
    def check(cls, current: ConfirmationState, target: ConfirmationState, cause: Cause) -> None:
        if not cls.is_allowed(current, target, cause):
            raise ConflictError(
                f"A confirmation cannot go from {current} to {target} ({cause})",
                details={
                    "code": "CONFIRMATION_TRANSITION_ILLEGAL",
                    "from": str(current),
                    "to": str(target),
                    "cause": str(cause),
                },
            )

    @classmethod
    def targets(cls, current: ConfirmationState) -> frozenset[ConfirmationState]:
        return frozenset(target for (source, target) in cls.transitions if source == current)

    @staticmethod
    def method_for(
        cause: Cause, seller_method: ConfirmationMethod | None = None
    ) -> ConfirmationMethod | None:
        """The ``method`` a move to a terminal state records."""
        if cause in _SELLER_CAUSES:
            method = seller_method or M.SELLER
            if method not in SELLER_METHODS:
                raise ValidationError(
                    "A seller can only confirm by review, by phone or in the chat",
                    details={"code": "CONFIRMATION_METHOD_INVALID", "method": str(method)},
                )
            return method
        if seller_method is not None:
            raise ValidationError(
                "Only a seller's answer carries a method",
                details={"code": "CONFIRMATION_METHOD_INVALID", "method": str(seller_method)},
            )
        return _METHOD_FOR.get(cause)
