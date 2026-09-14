"""Health endpoints.

Master spec section 116::

    /health/live    - the process is up
    /health/ready   - required dependencies are reachable and migrations current

Readiness checks the database, the migration revision and Redis when it is
configured. It deliberately does **not** call any courier: provider health is a
separate concern (section 49), and letting a courier outage mark the app
unhealthy would take the seller's own data offline for a problem that only
affects booking.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import system_session

router = APIRouter(prefix="/health", tags=["health"])

log = get_logger(__name__)


@router.get("/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    """Cheap and dependency-free, so a database blip never restarts the process."""
    return {"status": "ok", "service": "ecomsbd"}


@lru_cache(maxsize=1)
def migration_head() -> str | None:
    return ScriptDirectory(
        str(Path(__file__).resolve().parents[2] / "migrations")
    ).get_current_head()


@router.get("", summary="Production health")
@router.get("/ready", summary="Readiness probe")
async def ready(response: Response) -> dict[str, Any]:
    """Report whether this instance can serve traffic."""
    settings = get_settings()
    checks: dict[str, Any] = {}
    healthy = True

    try:
        async with system_session("health: readiness probe") as session:
            await session.execute(sa.text("SELECT 1"))
            revision = (
                await session.execute(sa.text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
        checks["database"] = {"status": "ok", "migration": revision}
        if revision is None or revision != migration_head():
            checks["database"] = {"status": "degraded", "reason": "migration is not at head"}
            healthy = False
    except Exception as exc:
        log.warning("readiness: database check failed", extra={"error_type": type(exc).__name__})
        checks["database"] = {"status": "error"}
        healthy = False

    if settings.redis_url:
        try:
            from app.common.cache import get_cache

            await get_cache().set("health:ready", "1", ttl_seconds=30)
            checks["redis"] = {"status": "ok"}
        except Exception as exc:
            log.warning("readiness: redis check failed", extra={"error_type": type(exc).__name__})
            checks["redis"] = {"status": "error"}
            healthy = False
    else:
        checks["redis"] = {"status": "not_configured"}

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ok" if healthy else "degraded", "checks": checks}


__all__ = ["router"]
