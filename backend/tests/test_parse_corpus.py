"""Parse corpus (V4 WP0.3): the deterministic parser over every case.

The corpus lives in ``tests/fixtures/parse_corpus``. Its ``expect`` blocks are
what a person reading the message would enter; the gate measures phone,
address and items.
"""

from __future__ import annotations

import pytest

from app.common.phone import try_normalize_bd_phone
from app.orders.parser import DeterministicOrderParser
from app.orders.parser.eval import (
    GATED_FIELDS,
    CorpusCase,
    gated_accuracy,
    grade,
    load_corpus,
    score,
)

CORPUS = load_corpus()


def test_the_corpus_has_the_spec_minimum() -> None:
    assert len(CORPUS) >= 50
    ids = [case.id for case in CORPUS]
    assert len(ids) == len(set(ids)), "duplicate case id"
    assert {f"seed-0{n}" for n in range(1, 6)} <= set(ids)
    for case in CORPUS:
        assert "phones" in case.expect, case.id
        assert case.llm is not None or case.llm_error is not None, case.id


@pytest.mark.parametrize("case", [c for c in CORPUS if c.id.startswith("seed-")], ids=str)
def test_seed_cases_parse_exactly_without_a_model(case: CorpusCase) -> None:
    graded = grade(case, DeterministicOrderParser().parse(case.text))
    assert all(graded.values()), {name: ok for name, ok in graded.items() if not ok}


@pytest.mark.parametrize("case", CORPUS, ids=lambda c: c.id)
def test_every_case_keeps_the_text_and_returns_only_real_numbers(case: CorpusCase) -> None:
    parsed = DeterministicOrderParser().parse(case.text)
    assert parsed.source_text == case.text
    for phone in parsed.phones:
        normalized = try_normalize_bd_phone(phone)
        assert normalized is not None and normalized.e164 == phone


def test_deterministic_accuracy_does_not_regress() -> None:
    parser = DeterministicOrderParser()
    totals = score(grade(case, parser.parse(case.text)) for case in CORPUS)
    # Measured 2026-11-18 at 95.8% on the gated fields; this is a floor, not
    # the gate (G0.3 is measured on the enhanced path).
    assert gated_accuracy(totals) >= 0.95, {
        name: f"{totals[name].correct}/{totals[name].total}" for name in GATED_FIELDS
    }
    assert totals["phones"].accuracy == 1.0
