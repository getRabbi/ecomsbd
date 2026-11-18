"""Field accuracy of the order parser over the parse corpus.

``python -m app.orders.parser.eval`` grades the deterministic parser and the
enhanced path with the corpus' recorded model answers (no network).
``python -m app.orders.parser.eval --live`` sends every case to the configured
provider instead — operator-run only, never in CI: it costs money and needs
``AI_PARSE_API_KEY``. It prints accuracy per field and never prints message
text, phones or the model's answers.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.common.phone import normalize_digits
from app.orders.parser.base import ParsedOrder

__all__ = [
    "DEFAULT_CORPUS_DIR",
    "GATED_FIELDS",
    "CorpusCase",
    "FieldScore",
    "grade",
    "load_corpus",
    "score",
]

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "parse_corpus"

#: Gate G0.3 is measured on these; the others are reported for information.
GATED_FIELDS = ("phones", "address", "items")
ALL_FIELDS = (*GATED_FIELDS, "customer_name", "cod_amount_paisa")


@dataclass(frozen=True, slots=True)
class CorpusCase:
    id: str
    text: str
    expect: dict[str, Any]
    #: The extraction-tool input the fake client returns, verbatim.
    llm: dict[str, Any] | None = None
    #: ``timeout`` / ``error``: the fake client fails instead of answering.
    llm_error: str | None = None


@dataclass(slots=True)
class FieldScore:
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0


def load_corpus(directory: Path = DEFAULT_CORPUS_DIR) -> list[CorpusCase]:
    cases: list[CorpusCase] = []
    for path in sorted(directory.glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            cases.append(
                CorpusCase(
                    id=str(raw["id"]),
                    text=str(raw["text"]),
                    expect=dict(raw.get("expect") or {}),
                    llm=raw.get("llm"),
                    llm_error=raw.get("llm_error"),
                )
            )
    return cases


def _norm_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", normalize_digits(value)).casefold()
    text = re.sub(r"[,.।:;()\-–—/]+", " ", text)
    return " ".join(text.split())


def _norm_items(items: Iterable[Any]) -> list[tuple[str, int, str]]:
    normalized = []
    for item in items:
        get = item.get if isinstance(item, dict) else lambda k, i=item: getattr(i, k)
        normalized.append(
            (
                _norm_text(get("name")),
                int(get("quantity") or 1),
                str(get("size") or "").upper(),
            )
        )
    return sorted(normalized)


def _expected_unit_prices(expected: list[dict[str, Any]]) -> list[int | None] | None:
    if not any("unit_price_paisa" in item for item in expected):
        return None
    return [item.get("unit_price_paisa") for item in expected]


def grade(case: CorpusCase, parsed: ParsedOrder) -> dict[str, bool]:
    """Per graded field, whether the parse matches the expectation."""
    expect = case.expect
    result: dict[str, bool] = {}
    if "phones" in expect:
        result["phones"] = sorted(parsed.phones) == sorted(expect["phones"])
    if "address" in expect:
        result["address"] = _norm_text(parsed.address) == _norm_text(expect["address"])
    if "items" in expect:
        same = _norm_items(parsed.items) == _norm_items(expect["items"])
        prices = _expected_unit_prices(expect["items"])
        if same and prices is not None:
            same = [item.unit_price_paisa for item in parsed.items] == prices
        result["items"] = same
    if "customer_name" in expect:
        result["customer_name"] = _norm_text(parsed.customer_name) == _norm_text(
            expect["customer_name"]
        )
    if "cod_amount_paisa" in expect:
        result["cod_amount_paisa"] = parsed.cod_amount_paisa == expect["cod_amount_paisa"]
    return result


def score(graded: Iterable[dict[str, bool]]) -> dict[str, FieldScore]:
    totals = {name: FieldScore() for name in ALL_FIELDS}
    for case in graded:
        for name, ok in case.items():
            totals[name].total += 1
            totals[name].correct += int(ok)
    return totals


def gated_accuracy(totals: dict[str, FieldScore]) -> float:
    correct = sum(totals[name].correct for name in GATED_FIELDS)
    total = sum(totals[name].total for name in GATED_FIELDS)
    return correct / total if total else 1.0


def _report(label: str, totals: dict[str, FieldScore]) -> None:
    print(f"{label}: gated (phones+address+items) {gated_accuracy(totals):.1%}")
    for name in ALL_FIELDS:
        entry = totals[name]
        print(f"  {name:<18} {entry.correct:>3}/{entry.total:<3} {entry.accuracy:.1%}")


async def _run(live: bool, corpus: Sequence[CorpusCase]) -> int:
    from app.orders.parser.deterministic import DeterministicOrderParser

    deterministic = DeterministicOrderParser()
    det_scores = score(grade(case, deterministic.parse(case.text)) for case in corpus)
    _report("deterministic", det_scores)

    if live:
        # The enhanced path (AiParseClient + merge) is the next WP0.3 slice.
        print("Live evaluation needs the enhanced parser, which is not built yet.")
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="call the configured provider")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.live, load_corpus(args.corpus)))


if __name__ == "__main__":
    sys.exit(main())
