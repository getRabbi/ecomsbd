"""Recording model calls in ``ai_usage``."""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_usage.models import AiUsage, AiUsageOutcome, AiUsagePurpose

__all__ = ["AiUsageService", "estimate_cost_micro_usd"]


def estimate_cost_micro_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    input_per_mtok_usd: Decimal | None,
    output_per_mtok_usd: Decimal | None,
) -> int | None:
    """Estimated cost in micro-USD, or ``None`` when a price is not configured.

    USD per million tokens is micro-USD per token, so the estimate is a plain
    weighted sum, kept in :class:`~decimal.Decimal` and rounded once.
    """
    if input_per_mtok_usd is None or output_per_mtok_usd is None:
        return None
    total = (
        Decimal(input_tokens) * input_per_mtok_usd + Decimal(output_tokens) * output_per_mtok_usd
    )
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


class AiUsageService:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        purpose: AiUsagePurpose,
        provider: str,
        model: str,
        outcome: AiUsageOutcome,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        cost_micro_usd: int | None = None,
        error_code: str | None = None,
    ) -> AiUsage:
        row = AiUsage(
            tenant_id=tenant_id,
            purpose=str(purpose),
            provider=provider[:32],
            model=model[:80],
            input_tokens=max(0, input_tokens),
            output_tokens=max(0, output_tokens),
            latency_ms=max(0, latency_ms),
            cost_micro_usd=cost_micro_usd,
            outcome=str(outcome),
            error_code=error_code[:32] if error_code else None,
        )
        self._db.add(row)
        await self._db.flush()
        return row
