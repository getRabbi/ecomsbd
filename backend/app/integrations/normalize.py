"""What a provider order may be turned into, and why it may not."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class Skip(Exception):
    """A provider order that is deliberately not imported (not a failure)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Reject(Exception):
    """A provider order that cannot become an ecomsbd order as it stands."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def paisa(value: Any) -> int:
    try:
        amount = Decimal(str(value if value is not None else "0"))
    except InvalidOperation as exc:
        raise Reject("ORDER_REJECTED_BY_MAPPING") from exc
    if not amount.is_finite():
        raise Reject("ORDER_REJECTED_BY_MAPPING")
    return max(0, int((amount * 100).quantize(Decimal("1"))))


def clip(value: Any, size: int) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:size] or None
