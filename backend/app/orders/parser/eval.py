"""Field accuracy of the order parser.

Two case sets:

*   ``--set corpus`` (default): ``tests/fixtures/parse_corpus``, written with
    the parser, so its score is optimistic. Without ``--live`` the merged path
    uses each case's recorded model answer (no network) - the CI regression.
*   ``--set holdout``: ``tests/fixtures/parse_holdout``, messages the parser
    was never tuned on. It has no recorded answers; gate G0.3 is measured on it
    with ``--live`` (operator correction 2026-11-18).

``--live`` sends every case that needs the model to the configured provider -
operator-run only, never in CI: it costs money and needs ``AI_PARSE_API_KEY``
and ``AI_PARSE_MODEL``. Deterministic-only and merged accuracy are printed
separately. Nothing prints message text, phones or the model's answers.
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
from app.orders.parser.llm_schema import validate_llm_output
from app.orders.parser.merge import merge, needs_model

__all__ = [
    "DEFAULT_CORPUS_DIR",
    "GATED_FIELDS",
    "HOLDOUT_DIR",
    "CorpusCase",
    "FieldScore",
    "grade",
    "load_corpus",
    "merged_with_recorded",
    "score",
]

_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
DEFAULT_CORPUS_DIR = _FIXTURES / "parse_corpus"
HOLDOUT_DIR = _FIXTURES / "parse_holdout"
SETS = {"corpus": DEFAULT_CORPUS_DIR, "holdout": HOLDOUT_DIR}

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


#: Words that only announce a size; "size 30" and size "30" are the same item.
_SIZE_WORDS = frozenset({"size", "সাইজ"})


def _norm_items(items: Iterable[Any]) -> list[tuple[tuple[str, ...], int]]:
    """Each item as (its words, quantity), however name/colour/size are split.

    "black hoodie" with size XXL and "black hoodie XXL" describe the same item,
    and so do a product with colour "blue" and "product blue"; a grader that
    cared how a parser split them would measure the split, not the reading.
    """
    normalized = []
    for item in items:
        get = item.get if isinstance(item, dict) else lambda k, i=item: getattr(i, k)
        words = {
            word
            for part in (get("name"), get("color"), get("size"))
            for word in _norm_text(str(part) if part is not None else None).split()
            if word not in _SIZE_WORDS
        }
        normalized.append((tuple(sorted(words)), int(get("quantity") or 1)))
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


def merged_with_recorded(case: CorpusCase, parsed: ParsedOrder) -> ParsedOrder:
    """The enhanced result using the case's recorded answer (no network)."""
    if not needs_model(parsed) or case.llm_error is not None:
        return parsed
    answer = validate_llm_output(case.llm)
    return merge(parsed, answer) if answer is not None else parsed


async def _merged_live(cases: Sequence[CorpusCase], parsed: list[ParsedOrder]) -> list[ParsedOrder]:
    from app.core.config import get_settings
    from app.orders.parser.ai_client import AiParseUnavailableError
    from app.orders.parser.enhanced import build_ai_parse_client

    client = build_ai_parse_client(get_settings())
    if client is None:
        raise SystemExit("AI_PARSE_API_KEY and AI_PARSE_MODEL must be set for --live")
    merged: list[ParsedOrder] = []
    asked = failed = invalid = 0
    for case, result in zip(cases, parsed, strict=True):
        if not needs_model(result):
            merged.append(result)
            continue
        asked += 1
        try:
            answer = validate_llm_output((await client.extract(case.text)).output)
        except AiParseUnavailableError as exc:
            failed += 1
            print(f"  {case.id}: unavailable ({exc.code})")
            merged.append(result)
            continue
        if answer is None:
            invalid += 1
            print(f"  {case.id}: answer broke the schema")
            merged.append(result)
            continue
        merged.append(merge(result, answer))
    print(f"model asked for {asked}/{len(cases)} cases ({failed} unavailable, {invalid} invalid)")
    return merged


def _scores(cases: Sequence[CorpusCase], results: Sequence[ParsedOrder]) -> dict[str, FieldScore]:
    return score(grade(case, result) for case, result in zip(cases, results, strict=True))


async def _run(live: bool, set_name: str, cases: Sequence[CorpusCase]) -> int:
    from app.orders.parser.deterministic import DeterministicOrderParser

    deterministic = DeterministicOrderParser()
    parsed = [deterministic.parse(case.text) for case in cases]
    print(f"set {set_name}: {len(cases)} cases")
    _report("deterministic only", _scores(cases, parsed))

    if live:
        _report("merged (live model)", _scores(cases, await _merged_live(cases, parsed)))
    elif any(case.llm is not None or case.llm_error is not None for case in cases):
        merged = [merged_with_recorded(c, p) for c, p in zip(cases, parsed, strict=True)]
        _report("merged (recorded answers)", _scores(cases, merged))
    else:
        print("merged: this set has no recorded answers; run with --live")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--live", action="store_true", help="call the configured provider")
    parser.add_argument("--set", dest="set_name", choices=sorted(SETS), default="corpus")
    parser.add_argument("--corpus", type=Path, default=None, help="override the set's directory")
    args = parser.parse_args(argv)
    directory = args.corpus or SETS[args.set_name]
    return asyncio.run(_run(args.live, args.set_name, load_corpus(directory)))


if __name__ == "__main__":
    sys.exit(main())
