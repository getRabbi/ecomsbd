"""The one shape a model's order extraction may take (V4 WP0.3).

The model gets no tools: it can only answer with JSON matching
:data:`EXTRACTION_JSON_SCHEMA`, and every answer is then validated again with
:class:`LlmParseOutput`. The second check is the one that counts — the API's
schema enforcement has no length or range limits, and a fake or a future
provider may not enforce anything. Any violation (an extra key such as
``"action"``, a 10 kB "name", a negative amount) discards the whole answer and
the deterministic parse stands (rule L12: the model only fills fields).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "EXTRACTION_JSON_SCHEMA",
    "LlmFieldConfidence",
    "LlmItem",
    "LlmParseOutput",
    "validate_llm_output",
]

_MAX_PAISA = 100_000_000  # ৳10,00,000 — the same ceiling the deterministic parser uses


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class LlmItem(_Strict):
    #: ``None`` when the customer gave a quantity but no product ("2ta nibo").
    name: str | None = Field(default=None, max_length=200)
    quantity: int | None = Field(default=None, ge=1, le=999)
    size: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=40)
    unit_price_paisa: int | None = Field(default=None, ge=0, le=_MAX_PAISA)


class LlmFieldConfidence(_Strict):
    customer_name: float | None = Field(default=None, ge=0, le=1)
    phones: float | None = Field(default=None, ge=0, le=1)
    address: float | None = Field(default=None, ge=0, le=1)
    items: float | None = Field(default=None, ge=0, le=1)
    cod_amount_paisa: float | None = Field(default=None, ge=0, le=1)


class LlmParseOutput(_Strict):
    customer_name: str | None = Field(default=None, max_length=160)
    phones: list[str] = Field(default_factory=list, max_length=5)
    address: str | None = Field(default=None, max_length=500)
    district: str | None = Field(default=None, max_length=80)
    thana: str | None = Field(default=None, max_length=80)
    items: list[LlmItem] = Field(default_factory=list, max_length=20)
    cod_amount_paisa: int | None = Field(default=None, ge=0, le=_MAX_PAISA)
    notes: str | None = Field(default=None, max_length=500)
    field_confidence: LlmFieldConfidence = Field(default_factory=LlmFieldConfidence)


def validate_llm_output(raw: Any) -> LlmParseOutput | None:
    """The validated answer, or ``None`` when it breaks the schema in any way."""
    if not isinstance(raw, dict):
        return None
    phones = raw.get("phones")
    if isinstance(phones, list) and any(
        not isinstance(phone, str) or len(phone) > 24 for phone in phones
    ):
        return None
    try:
        return LlmParseOutput.model_validate(raw)
    except ValidationError:
        return None


def _nullable(kind: str) -> dict[str, Any]:
    return {"anyOf": [{"type": kind}, {"type": "null"}]}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


#: Sent as ``output_config.format.schema``. Every key is required (nullable
#: instead of optional) because structured outputs need ``required`` and
#: ``additionalProperties: false`` on every object; bounds live in Pydantic.
EXTRACTION_JSON_SCHEMA: dict[str, Any] = _object(
    {
        "customer_name": _nullable("string"),
        "phones": {"type": "array", "items": {"type": "string"}},
        "address": _nullable("string"),
        "district": _nullable("string"),
        "thana": _nullable("string"),
        "items": {
            "type": "array",
            "items": _object(
                {
                    "name": _nullable("string"),
                    "quantity": _nullable("integer"),
                    "size": _nullable("string"),
                    "color": _nullable("string"),
                    "unit_price_paisa": _nullable("integer"),
                }
            ),
        },
        "cod_amount_paisa": _nullable("integer"),
        "notes": _nullable("string"),
        "field_confidence": _object(
            {
                "customer_name": _nullable("number"),
                "phones": _nullable("number"),
                "address": _nullable("number"),
                "items": _nullable("number"),
                "cod_amount_paisa": _nullable("number"),
            }
        ),
    }
)
