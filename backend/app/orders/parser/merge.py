"""When to ask the model, and how its answer meets the deterministic parse.

Operator rules (V4 WP0.3 correction, 2026-11-18):

*   The deterministic parser is trusted for **phones** and for fields it read
    from an explicit label — confidence ``>= HIGH`` (0.8) means exactly that.
*   The model is asked unless name, address, items and COD all came from
    labels. An empty field is confidence 0, so it always asks.
*   Phones stay deterministic. A model phone is added only when it is a valid
    Bangladeshi mobile **and** the same number is in the source text, so the
    model can never introduce a number the customer did not write.
*   For name/address/items/COD: a labelled deterministic value is kept;
    otherwise the model's value (including "not stated") replaces the guess.
    A labelled value the model reads differently stays, and the field is
    listed in ``uncertain_fields`` so the seller looks at it.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from typing import Any

from app.common.phone import extract_phones, normalize_digits, try_normalize_bd_phone
from app.orders.parser.base import FieldConfidence, ParsedItem, ParsedOrder
from app.orders.parser.districts import find_district
from app.orders.parser.llm_schema import LlmParseOutput

__all__ = ["LABELLED", "MODEL_CONFIDENCE_CAP", "merge", "needs_model"]

#: At or above this, a deterministic field came from a label.
LABELLED = FieldConfidence.HIGH
#: A model's value is a suggestion, never reported as labelled or exact.
MODEL_CONFIDENCE_CAP = 0.75

#: Confidence key -> (ParsedOrder attribute, LLM field_confidence key).
_MERGED_FIELDS: dict[str, tuple[str, str]] = {
    "name": ("customer_name", "customer_name"),
    "address": ("address", "address"),
    "items": ("items", "items"),
    "amount": ("cod_amount_paisa", "cod_amount_paisa"),
}

#: Deterministic warnings that stop being true once a field is replaced.
_FIELD_WARNINGS: dict[str, str] = {
    "Only a district found — add the full address": "address",
    "No delivery address recognised": "address",
    "No product line recognised": "items",
    "Several amounts found — check the COD value": "amount",
}


def needs_model(parsed: ParsedOrder) -> bool:
    """Whether any non-phone field is a guess or missing."""
    return any(parsed.confidence.get(key, 0.0) < LABELLED for key in _MERGED_FIELDS)


def _norm(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFC", normalize_digits(value)).casefold()
    text = re.sub(r"[,.।:;()\-–—/]+", " ", text)
    return " ".join(text.split())


def _items_key(items: list[ParsedItem]) -> list[tuple[str, int]]:
    return sorted((_norm(item.name), item.quantity) for item in items)


def _same(key: str, left: Any, right: Any) -> bool:
    if key == "items":
        return bool(_items_key(left) == _items_key(right))
    if key == "amount":
        return bool(left == right)
    return _norm(left) == _norm(right)


def _present(value: Any) -> bool:
    return bool(value) if not isinstance(value, int) else True


def _model_items(answer: LlmParseOutput) -> list[ParsedItem]:
    return [
        ParsedItem(
            name=item.name or "",
            quantity=item.quantity or 1,
            size=item.size,
            color=item.color,
            unit_price_paisa=item.unit_price_paisa,
        )
        for item in answer.items
    ]


def _model_value(key: str, answer: LlmParseOutput) -> Any:
    if key == "name":
        return answer.customer_name or None
    if key == "address":
        return answer.address or None
    if key == "items":
        return _model_items(answer)
    return answer.cod_amount_paisa


def _model_confidence(key: str, answer: LlmParseOutput, value: Any) -> float:
    if not _present(value):
        return FieldConfidence.NONE
    stated = getattr(answer.field_confidence, _MERGED_FIELDS[key][1])
    base = FieldConfidence.MEDIUM if stated is None else stated
    return min(base, MODEL_CONFIDENCE_CAP)


def _verified_model_phones(answer: LlmParseOutput, text: str) -> list[str]:
    """Model phones that are valid and literally in the text, canonical form."""
    in_text = {phone.e164 for phone in extract_phones(text)}
    verified: list[str] = []
    for raw in answer.phones:
        number = try_normalize_bd_phone(raw)
        if number is not None and number.e164 in in_text and number.e164 not in verified:
            verified.append(number.e164)
    return verified


def merge(parsed: ParsedOrder, answer: LlmParseOutput) -> ParsedOrder:
    """A new :class:`ParsedOrder`; ``parsed`` is not modified."""
    result = dataclasses.replace(
        parsed,
        phones=list(parsed.phones),
        items=list(parsed.items),
        confidence=dict(parsed.confidence),
        warnings=list(parsed.warnings),
        uncertain_fields=list(parsed.uncertain_fields),
        parser="enhanced",
    )

    added = [
        p for p in _verified_model_phones(answer, parsed.source_text) if p not in result.phones
    ]
    if added:
        result.phones.extend(added)
        if parsed.confidence.get("phone", 0.0) <= FieldConfidence.NONE:
            result.confidence["phone"] = FieldConfidence.MEDIUM
            result.warnings = [
                w for w in result.warnings if w != "No Bangladeshi mobile number found"
            ]
        result.selected_phone = result.phones[0] if len(result.phones) == 1 else None

    replaced: set[str] = set()
    for key, (attribute, _) in _MERGED_FIELDS.items():
        current = getattr(parsed, attribute)
        proposed = _model_value(key, answer)
        if parsed.confidence.get(key, 0.0) >= LABELLED and _present(current):
            if _present(proposed) and not _same(key, current, proposed):
                _mark_uncertain(result, key)
            continue
        setattr(result, attribute, proposed)
        result.confidence[key] = _model_confidence(key, answer, proposed)
        replaced.add(key)

    if "address" in replaced:
        result.district = find_district(result.address) or find_district(answer.district)
        result.thana = answer.thana if result.address else None
    if not result.notes and answer.notes:
        result.notes = answer.notes
    result.warnings = [w for w in result.warnings if _FIELD_WARNINGS.get(w) not in replaced]
    return result


def _mark_uncertain(result: ParsedOrder, key: str) -> None:
    if key not in result.uncertain_fields:
        result.uncertain_fields.append(key)
