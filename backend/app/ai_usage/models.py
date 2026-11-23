"""``ai_usage``: one row per model call a shop's request caused.

No message text, phone, name or model answer is stored — only what the call
cost and how it ended. ``cost_micro_usd`` is an **estimate** from configured
per-token prices; ``NULL`` means the price was not configured, not "free".
"""

from __future__ import annotations

from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PrimaryKeyMixin, TenantOwned, TimestampMixin

__all__ = ["AiUsage", "AiUsageOutcome", "AiUsagePurpose"]


class AiUsagePurpose(StrEnum):
    ORDER_PARSE = "order_parse"


class AiUsageOutcome(StrEnum):
    #: Answer received and it passed the schema.
    OK = "ok"
    #: Answer received but broke the schema; discarded.
    INVALID = "invalid"
    #: No usable answer (timeout, HTTP error, refusal ...); see ``error_code``.
    ERROR = "error"


class AiUsage(Base, TenantOwned, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_usage"
    __table_args__ = (
        sa.Index("ix_ai_usage_tenant_purpose_created", "tenant_id", "purpose", "created_at"),
    )

    purpose: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    input_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cost_micro_usd: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    latency_ms: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    #: Machine code only (``timeout``, ``http_529`` ...), never provider text.
    error_code: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
