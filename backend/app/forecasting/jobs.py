"""The daily forecast snapshot (V3.6)."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger
from app.db.session import session_scope
from app.forecasting.service import ForecastService
from app.notifications.jobs import _active_tenants

log = get_logger(__name__)


async def snapshot_demand_forecasts(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Store each active shop's forecasts for today, once.

    Runs before the morning alert scan so a predicted stock-out is on the
    snapshot the alert reads. Each shop in its own session: one shop failing
    does not cost the others their forecast.
    """
    stored = announced = failed = 0
    for tenant_id, _timezone in await _active_tenants():
        token = set_context(
            RequestContext(
                trace_id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                actor_type=ActorType.SYSTEM,
                job_name="forecast:snapshot",
            )
        )
        try:
            async with session_scope() as db:
                result = await ForecastService(db).snapshot()
            stored += result["stored"]
            announced += result["announced"]
        except Exception as exc:
            failed += 1
            log.exception(
                "forecast snapshot failed for one shop",
                extra={"tenant_id": str(tenant_id), "error": type(exc).__name__},
            )
        finally:
            clear_context(token)
    return {"stored": stored, "announced": announced, "failed": failed}
