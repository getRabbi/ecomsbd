"""``/v1/realtime``: a socket that tells signed-in clients when to refetch.

Invalidation only. A message names a topic ("orders changed"), never the
change; the client asks the usual endpoints again, with the usual permissions.
PostgreSQL behind this API stays the one source of truth, and a client that
misses a message (asleep, offline, mid-reconnect) still converges on its next
ordinary refresh.

The protocol, in order:

1.  The client opens the socket. **No credential in the URL**: query strings
    end up in proxy and access logs.
2.  Within ten seconds it sends ``{"type": "auth", "access_token": "..."}``.
    The token goes through :func:`app.api.deps.authenticate_access_token`, the
    same check every HTTP request gets.
3.  The server answers ``{"type": "ready", "shop_id": ...}`` and subscribes the
    socket to that shop, and only that shop. The shop comes from the session,
    never from the client.
4.  The server then sends ``invalidate``, ``resync`` (refetch everything) and
    ``ping`` messages. The client may send a fresh ``auth`` after its token
    refreshes; a socket whose token expires is closed.

Close codes: 4401 sign in again, 4403 no shop or not a member, 4409 the
session moved to another shop (reconnect now), 4400/4408/4429 protocol errors,
1013 realtime switched off.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass

import jwt
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.deps import authenticate_access_token
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ForbiddenError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.realtime.hub import (
    CLOSE_BAD_MESSAGE,
    CLOSE_FORBIDDEN,
    CLOSE_REBIND,
    CLOSE_UNAUTHENTICATED,
    Close,
    Subscriber,
    get_hub,
)

router = APIRouter(tags=["realtime"])

log = get_logger(__name__)

AUTH_TIMEOUT_SECONDS = 10.0
HEARTBEAT_SECONDS = 25.0
#: How often a long-lived socket re-runs the full session check. Revocation
#: and shop switches close sockets at once through the control channel; this
#: is the backstop if one of those messages was lost.
REVALIDATE_SECONDS = 300.0
MAX_MESSAGE_BYTES = 16 * 1024
MAX_MESSAGES_PER_MINUTE = 30

_CLOSE_AUTH_TIMEOUT = 4408
_CLOSE_TOO_MANY = 4429
_CLOSE_UNAVAILABLE = 1013


class _Refused(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass
class _Grant:
    session_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    expires_at: float


def _token_expiry(token: str) -> float:
    # Read only after authenticate_access_token has verified the signature.
    claims = jwt.decode(token, options={"verify_signature": False})
    return float(claims["exp"])


async def _authenticate(settings: Settings, token: str) -> _Grant:
    try:
        async with session_scope() as db:
            principal = await authenticate_access_token(db, settings, token)
            grant = _Grant(
                session_id=principal.session_id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                expires_at=_token_expiry(token),
            )
            await db.flush()
        return grant
    except ForbiddenError as exc:
        raise _Refused(CLOSE_FORBIDDEN, "forbidden") from exc
    except AppError as exc:
        if exc.http_status >= 500:
            # The identity provider's keys could not be read. Not the
            # client's fault, and not a verdict on its token.
            raise _Refused(_CLOSE_UNAVAILABLE, "try again later") from exc
        raise _Refused(CLOSE_UNAUTHENTICATED, "unauthenticated") from exc
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise _Refused(CLOSE_UNAUTHENTICATED, "unauthenticated") from exc


def _origin_allowed(websocket: WebSocket, settings: Settings) -> bool:
    """Browsers send Origin on a socket and CORS does not apply; check it here.

    Native apps send none, and the token, not the origin, is what authenticates.
    """
    origin = websocket.headers.get("origin")
    allowed = settings.cors_allow_origins
    if origin is None or not allowed or "*" in allowed:
        return True
    return origin in allowed


def _token_from(raw: str) -> str:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise _Refused(CLOSE_BAD_MESSAGE, "bad message") from exc
    if not isinstance(data, dict) or data.get("type") != "auth":
        raise _Refused(CLOSE_UNAUTHENTICATED, "auth required")
    token = data.get("access_token")
    if not isinstance(token, str) or not token or len(token) > MAX_MESSAGE_BYTES:
        raise _Refused(CLOSE_UNAUTHENTICATED, "auth required")
    return token


def _ready(tenant_id: uuid.UUID) -> str:
    return json.dumps(
        {"type": "ready", "shop_id": str(tenant_id), "heartbeat_seconds": int(HEARTBEAT_SECONDS)},
        separators=(",", ":"),
    )


_PING = json.dumps({"type": "ping"}, separators=(",", ":"))
_PONG = json.dumps({"type": "pong"}, separators=(",", ":"))


@dataclass
class _Connection:
    settings: Settings
    subscriber: Subscriber
    token: str
    expires_at: float
    revalidate_at: float


async def _read_client(websocket: WebSocket, state: _Connection) -> Close | None:
    """Fresh tokens and pings from the client. Returns how to close, if at all."""
    window_started = time.monotonic()
    count = 0
    hub = get_hub()
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        text = message.get("text")
        if not isinstance(text, str) or len(text) > MAX_MESSAGE_BYTES:
            return Close(CLOSE_BAD_MESSAGE, "bad message")
        now = time.monotonic()
        if now - window_started > 60:
            window_started, count = now, 0
        count += 1
        if count > MAX_MESSAGES_PER_MINUTE:
            return Close(_CLOSE_TOO_MANY, "too many messages")
        try:
            data = json.loads(text)
        except ValueError:
            return Close(CLOSE_BAD_MESSAGE, "bad message")
        kind = data.get("type") if isinstance(data, dict) else None
        if kind == "ping":
            state.subscriber.deliver(_PONG)
        elif kind == "auth":
            try:
                token = _token_from(text)
                grant = await _authenticate(state.settings, token)
            except _Refused as refused:
                return Close(refused.code, refused.reason)
            subscriber = state.subscriber
            if grant.session_id != subscriber.session_id or grant.user_id != subscriber.user_id:
                return Close(CLOSE_REBIND, "different session")
            if grant.tenant_id is None:
                return Close(CLOSE_FORBIDDEN, "no shop")
            state.token = token
            state.expires_at = grant.expires_at
            state.revalidate_at = time.time() + REVALIDATE_SECONDS
            if grant.tenant_id != subscriber.tenant_id:
                hub.rebind(subscriber, grant.tenant_id)
            subscriber.deliver(_ready(grant.tenant_id))
        # Anything else is ignored, so a newer client can add message types.


async def _write_client(websocket: WebSocket, state: _Connection) -> Close | None:
    """Invalidations, heartbeats, expiry and the periodic session re-check."""
    subscriber = state.subscriber
    while True:
        now = time.time()
        wait = min(HEARTBEAT_SECONDS, state.expires_at - now, state.revalidate_at - now)
        try:
            item = await asyncio.wait_for(subscriber.outbox.get(), timeout=max(wait, 0.0))
        except TimeoutError:
            item = None
        if isinstance(item, Close):
            return item
        if item is not None:
            await websocket.send_text(item)
            continue
        now = time.time()
        if now >= state.expires_at:
            return Close(CLOSE_UNAUTHENTICATED, "token expired")
        if now >= state.revalidate_at:
            try:
                grant = await _authenticate(state.settings, state.token)
            except _Refused as refused:
                return Close(refused.code, refused.reason)
            if grant.tenant_id != subscriber.tenant_id or grant.session_id != subscriber.session_id:
                return Close(CLOSE_REBIND, "shop changed")
            state.revalidate_at = now + REVALIDATE_SECONDS
        await websocket.send_text(_PING)


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    if websocket.application_state == WebSocketState.CONNECTED:
        with suppress(Exception):
            await websocket.close(code=code, reason=reason)


@router.websocket("/realtime")
async def realtime_socket(websocket: WebSocket) -> None:
    settings = get_settings()
    if not _origin_allowed(websocket, settings):
        # Before accept: the handshake itself is refused with a 403.
        await websocket.close(code=CLOSE_FORBIDDEN)
        return
    await websocket.accept()
    if not settings.realtime_enabled:
        await _close(websocket, _CLOSE_UNAVAILABLE, "realtime is off")
        return

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
        token = _token_from(raw)
        grant = await _authenticate(settings, token)
    except TimeoutError:
        await _close(websocket, _CLOSE_AUTH_TIMEOUT, "auth timeout")
        return
    except WebSocketDisconnect:
        return
    except _Refused as refused:
        await _close(websocket, refused.code, refused.reason)
        return
    except (KeyError, RuntimeError):
        # A binary frame, or a disconnect mid-handshake.
        await _close(websocket, CLOSE_BAD_MESSAGE, "bad message")
        return

    if grant.tenant_id is None:
        await _close(websocket, CLOSE_FORBIDDEN, "no shop")
        return

    hub = get_hub()
    subscriber = Subscriber(
        session_id=grant.session_id, user_id=grant.user_id, tenant_id=grant.tenant_id
    )
    state = _Connection(
        settings=settings,
        subscriber=subscriber,
        token=token,
        expires_at=grant.expires_at,
        revalidate_at=time.time() + REVALIDATE_SECONDS,
    )
    hub.add(subscriber)
    opened = time.monotonic()
    log.info(
        "realtime socket opened",
        extra={"tenant_id": str(grant.tenant_id), "session_id": str(grant.session_id)},
    )
    outcome: Close | None = None
    try:
        await websocket.send_text(_ready(grant.tenant_id))
        reader = asyncio.create_task(_read_client(websocket, state))
        writer = asyncio.create_task(_write_client(websocket, state))
        done, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        for task in done:
            with suppress(Exception):
                outcome = task.result() or outcome
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(subscriber)
        subscriber.closed = True
        if outcome is not None:
            await _close(websocket, outcome.code, outcome.reason)
        log.info(
            "realtime socket closed",
            extra={
                "tenant_id": str(subscriber.tenant_id),
                "session_id": str(subscriber.session_id),
                "close_code": outcome.code if outcome else None,
                "open_seconds": round(time.monotonic() - opened, 1),
            },
        )
