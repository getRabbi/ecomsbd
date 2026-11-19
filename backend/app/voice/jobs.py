"""Outbox handlers for voice topics (V4 WP3.3).

``voice.intro_requested`` → make the shop's intro with the TTS port, in the
shop's own tenant scope (the dispatcher has put the event's tenant into the
context). Idempotent: a name already recorded, or one the shop no longer has,
makes nothing; a retryable TTS failure raises so the outbox tries again.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import OutboxEvent, OutboxTopic
from app.core.logging import get_logger
from app.db.session import session_scope
from app.worker.jobs import register_handler

__all__: list[str] = []

log = get_logger("app.voice")


@register_handler(OutboxTopic.VOICE_INTRO_REQUESTED)
async def _on_intro_requested(_session: AsyncSession, event: OutboxEvent) -> None:
    from app.voice.intro import IntroAudioService

    if event.tenant_id is None:
        log.warning("intro request without a shop", extra={"event_id": str(event.id)})
        return
    key = str((event.payload or {}).get("shop_name_key") or "")
    async with session_scope() as db:
        row = await IntroAudioService(db).generate(event.tenant_id, key)
    log.info(
        "intro request handled",
        extra={"tenant_id": str(event.tenant_id), "made": row is not None},
    )
