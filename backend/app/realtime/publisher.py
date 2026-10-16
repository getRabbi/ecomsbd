"""Turn committed changes into invalidation events.

One hook on the SQLAlchemy session, rather than a call in each route: every
write path (API routes, ``/sync/mutations``, imports, the worker's courier
sync) commits through a session, so every one of them is covered, and a new
endpoint cannot forget to announce its changes.

*   ``after_flush`` notes which topics changed for which shop. The tenant is
    read from the row itself, so a worker's system session is attributed to
    the right shop.
*   ``after_commit`` hands the notes to a background task that publishes
    them. Nothing is published for a transaction that rolls back, and the
    commit never waits on Redis.
*   Publishing failures are logged and dropped. An invalidation is a hint to
    refetch sooner; clients still refresh on focus and on a timer, so a lost
    one costs freshness, never correctness, and a broken Redis can never fail
    a sale.

Events carry a topic, an opaque entity id and a version: no names, phone
numbers, addresses, amounts or message text.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import InstanceState, Session, SessionTransaction

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.ids import new_id
from app.core.logging import get_logger
from app.realtime.broker import CONTROL_CHANNEL, get_broker, tenant_channel
from app.realtime.topics import PRIMARY_TABLES, TABLE_TOPICS, RealtimeTopic

__all__ = ["drain", "install_realtime_hooks"]

log = get_logger(__name__)

_PENDING_KEY = "ecomsbd_realtime_pending"
#: Redis that does not answer within this is treated as down for this batch.
_PUBLISH_TIMEOUT_SECONDS = 2.0


@dataclass
class _TopicChange:
    primary_ids: set[uuid.UUID] = field(default_factory=set)
    version: int | None = None


@dataclass
class _Pending:
    topics: dict[tuple[uuid.UUID, RealtimeTopic], _TopicChange] = field(default_factory=dict)
    controls: list[dict[str, Any]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.topics or self.controls)


def _table_name(obj: object) -> str | None:
    state: InstanceState[Any] | None = sa.inspect(obj, raiseerr=False)
    if state is None:
        return None
    return getattr(state.mapper.local_table, "name", None)


def _changed(obj: object, attribute: str) -> tuple[bool, Any]:
    """Whether ``attribute`` changed in this flush, and its value before."""
    state: InstanceState[Any] | None = sa.inspect(obj, raiseerr=False)
    if state is None:
        return False, None
    history = state.attrs[attribute].history
    if not history.added and not history.deleted:
        return False, None
    before = history.deleted[0] if history.deleted else _UNKNOWN
    after = history.added[0] if history.added else None
    if before is not _UNKNOWN and before == after:
        return False, None
    return True, before


_UNKNOWN = object()


def _record_control(pending: _Pending, obj: object, *, deleted: bool) -> None:
    """Session, membership and account changes the socket hub must act on."""
    from app.auth.models import AuthSession
    from app.tenants.models import TenantUser
    from app.users.models import User

    if isinstance(obj, AuthSession):
        if deleted:
            pending.controls.append({"kind": "session", "session_id": str(obj.id), "revoked": True})
            return
        revoked, _ = _changed(obj, "revoked_at")
        if revoked and obj.revoked_at is not None:
            pending.controls.append({"kind": "session", "session_id": str(obj.id), "revoked": True})
            return
        moved, before = _changed(obj, "tenant_id")
        # A session's first shop (None -> shop) has no socket bound to an old
        # one; anything else is a switch, and the old shop's feed must stop.
        if moved and before is not None:
            pending.controls.append(
                {
                    "kind": "session",
                    "session_id": str(obj.id),
                    "revoked": False,
                    "tenant_id": str(obj.tenant_id) if obj.tenant_id else None,
                }
            )
    elif isinstance(obj, TenantUser):
        role_changed, _ = _changed(obj, "role")
        active_changed, _ = _changed(obj, "is_active")
        if deleted or role_changed or active_changed:
            pending.controls.append(
                {"kind": "member", "tenant_id": str(obj.tenant_id), "user_id": str(obj.user_id)}
            )
    elif isinstance(obj, User):
        status_changed, _ = _changed(obj, "status")
        deleted_changed, _ = _changed(obj, "deleted_at")
        if deleted or status_changed or deleted_changed:
            pending.controls.append({"kind": "user", "user_id": str(obj.id)})


def _record(
    pending: _Pending, obj: object, *, created: bool = False, deleted: bool = False
) -> None:
    table = _table_name(obj)
    if table is None:
        return
    # A row created in this flush has no socket bound to it yet.
    if not created and table in ("auth_sessions", "tenant_users", "users"):
        _record_control(pending, obj, deleted=deleted)
    topic = TABLE_TOPICS.get(table)
    if topic is None:
        return
    tenant_id = getattr(obj, "id", None) if table == "tenants" else getattr(obj, "tenant_id", None)
    if not isinstance(tenant_id, uuid.UUID):
        return
    change = pending.topics.setdefault((tenant_id, topic), _TopicChange())
    if PRIMARY_TABLES.get(topic) == table:
        entity_id = getattr(obj, "id", None)
        if isinstance(entity_id, uuid.UUID) and len(change.primary_ids) < 2:
            change.primary_ids.add(entity_id)
        version = getattr(obj, "version", None)
        change.version = version if isinstance(version, int) else None


def _after_flush(session: Session, _flush_context: Any) -> None:
    # Never raise from here: an exception would fail the caller's flush, and
    # with it a sale, over a refresh hint.
    try:
        if not get_settings().realtime_enabled:
            return
        pending = session.info.get(_PENDING_KEY)
        if pending is None:
            pending = session.info[_PENDING_KEY] = _Pending()
        for obj in session.new:
            _record(pending, obj, created=True)
        for obj in session.dirty:
            if session.is_modified(obj, include_collections=False):
                _record(pending, obj)
        for obj in session.deleted:
            _record(pending, obj, deleted=True)
    except Exception as exc:
        log.warning("realtime: change capture failed", extra={"error_type": type(exc).__name__})


def _messages(pending: _Pending) -> list[tuple[str, str]]:
    occurred_at = utc_now().isoformat()
    messages: list[tuple[str, str]] = [
        (CONTROL_CHANNEL, json.dumps(control, separators=(",", ":")))
        for control in pending.controls
    ]
    for (tenant_id, topic), change in pending.topics.items():
        single = len(change.primary_ids) == 1
        body = {
            "type": "invalidate",
            "topic": str(topic),
            "entity_id": str(next(iter(change.primary_ids))) if single else None,
            "version": change.version if single else None,
            "event_id": str(new_id()),
            "occurred_at": occurred_at,
        }
        messages.append((tenant_channel(tenant_id), json.dumps(body, separators=(",", ":"))))
    return messages


_background: set[asyncio.Task[None]] = set()


async def _publish(messages: list[tuple[str, str]]) -> None:
    try:
        broker = get_broker()
        for channel, message in messages:
            await asyncio.wait_for(broker.publish(channel, message), _PUBLISH_TIMEOUT_SECONDS)
    except Exception as exc:
        # The rest of this batch would meet the same outage. Clients catch up
        # on their next refresh.
        log.warning("realtime: publish failed", extra={"error_type": type(exc).__name__})


def _after_commit(session: Session) -> None:
    try:
        pending: _Pending | None = session.info.pop(_PENDING_KEY, None)
        if not pending:
            return
        messages = _messages(pending)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # A synchronous script (a migration, a one-off): no socket is
            # listening in this process, and nothing awaits a publish here.
            return
        task = loop.create_task(_publish(messages))
        _background.add(task)
        task.add_done_callback(_background.discard)
    except Exception as exc:
        log.warning("realtime: publish scheduling failed", extra={"error_type": type(exc).__name__})


def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    # The outermost transaction ended without `after_commit` taking the notes:
    # it rolled back, and nothing it changed may be announced.
    if transaction.parent is None:
        session.info.pop(_PENDING_KEY, None)


async def drain() -> None:
    """Wait for publishes already scheduled. Tests and shutdown."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


_INSTALLED = False


def install_realtime_hooks() -> None:
    """Attach the hooks to every session. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_transaction_end", _after_transaction_end)
    _INSTALLED = True
