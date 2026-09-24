"""The one place network benchmark privacy thresholds live (V3.7).

Every published network figure goes through :func:`publish_rate` or
:func:`publish_median`. Both take per-shop contributions (already grouped by
independent owner in SQL) and either return a coarse, rounded figure or
``None``, which the caller publishes as ``DATA_NOT_SUFFICIENT``.

The defences, all applied together:

* **Minimum cohort.** At least :data:`MIN_SHOPS` shops, each with at least
  :data:`MIN_SHOP_SAMPLE` observations, and :data:`MIN_SAMPLE` in total.
* **Contribution clipping and dominance.** A shop's weight is capped at
  :data:`MAX_SHOP_SAMPLE`, and no shop may make up more than
  :data:`MAX_SHARE_PERCENT` of the clipped total.
* **Both outcomes present.** A rate needs :data:`MIN_OUTCOME` on each side, so
  "everyone returned" or "no one returned" is never published.
* **Coarse output.** Rates are rounded to :data:`RATE_PRECISION_PERCENT`;
  durations and money to fixed steps; sample sizes only as bands. No exact
  count, shop identity or per-shop value leaves this module.

These are never lowered to make a figure appear.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

MIN_SHOPS = 20
MIN_SAMPLE = 200
MIN_OUTCOME = 20
MIN_SHOP_SAMPLE = 10
MAX_SHOP_SAMPLE = 100
MAX_SHARE_PERCENT = 10
RATE_PRECISION_PERCENT = 5

DATA_NOT_SUFFICIENT = "DATA_NOT_SUFFICIENT"
PUBLISHED = "PUBLISHED"


@dataclass(frozen=True)
class Published:
    value: int
    precision: int
    unit: str
    shops_band: str
    sample_band: str


def shops_band(count: int) -> str:
    if count >= 100:
        return "100+"
    if count >= 50:
        return "50-99"
    return "20-49"


def sample_band(count: int) -> str:
    for low, high in ((200, 499), (500, 999), (1000, 4999)):
        if low <= count <= high:
            return f"{low}-{high}"
    return "5000+"


def _round_to(value: float, step: int) -> int:
    return int(round(value / step) * step)


def publish_rate(contributions: list[tuple[int, int]]) -> Published | None:
    """``contributions`` are (observations, hits) per independent owner."""
    samples: list[tuple[int, int]] = []
    for total, hits in contributions:
        if total < 0 or not 0 <= hits <= total:
            return None
        if total < MIN_SHOP_SAMPLE:
            continue
        bounded = min(MAX_SHOP_SAMPLE, total)
        samples.append((bounded, hits * bounded // total))
    total = sum(n for n, _ in samples)
    hits = sum(h for _, h in samples)
    if (
        len(samples) < MIN_SHOPS
        or total < MIN_SAMPLE
        or any(n * 100 > total * MAX_SHARE_PERCENT for n, _ in samples)
        or min(hits, total - hits) < MIN_OUTCOME
    ):
        return None
    return Published(
        value=_round_to(hits * 100 / total, RATE_PRECISION_PERCENT),
        precision=RATE_PRECISION_PERCENT,
        unit="PERCENT",
        shops_band=shops_band(len(samples)),
        sample_band=sample_band(total),
    )


def publish_median(contributions: list[list[float]], *, step: int, unit: str) -> Published | None:
    """Median of per-shop medians, rounded to ``step``.

    Each shop contributes one value, its own median, so no shop can dominate;
    a shop with fewer than :data:`MIN_SHOP_SAMPLE` observations is left out.
    """
    per_shop = [median(values) for values in contributions if len(values) >= MIN_SHOP_SAMPLE]
    total = sum(min(MAX_SHOP_SAMPLE, len(v)) for v in contributions if len(v) >= MIN_SHOP_SAMPLE)
    if len(per_shop) < MIN_SHOPS or total < MIN_SAMPLE or any(v < 0 for v in per_shop):
        return None
    return Published(
        value=_round_to(median(per_shop), step),
        precision=step,
        unit=unit,
        shops_band=shops_band(len(per_shop)),
        sample_band=sample_band(total),
    )
