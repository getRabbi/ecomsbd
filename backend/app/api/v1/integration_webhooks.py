"""Unauthenticated integration traffic: OAuth callbacks and provider webhooks.

Kept under their own prefixes so no seller route with a path parameter can
shadow them. Every route is rate limited before it reads the body, and none of
them says whether a token or a shop exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from app.api.deps import DbSession, get_hasher
from app.common.cache import RateLimiter
from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.integrations import meta, receiver

callbacks = APIRouter(prefix="/integration-callbacks", tags=["integrations"])
webhooks = APIRouter(prefix="/webhooks/integrations", tags=["integrations"])


async def _limit(scope: str, identity: str, limit: int) -> None:
    result = await RateLimiter().hit(scope, identity, limit=limit, window_seconds=60)
    if not result.allowed:
        raise RateLimitedError(retry_after_seconds=result.retry_after_seconds)


def _ip(request: Request) -> str:
    return get_hasher().ip_hash(request.client.host if request.client else "unknown") or "unknown"


async def _body(request: Request) -> bytes | None:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > receiver.MAX_BODY:
        return None
    body = await request.body()
    return None if len(body) > receiver.MAX_BODY else body


def _headers(request: Request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.headers.items()}


def _finish(connection_id: object, result: str) -> Response:
    web = (get_settings().public_web_url or "").rstrip("/")
    if web and connection_id is not None:
        return RedirectResponse(f"{web}/integrations/{connection_id}?result={result}", 303)
    return JSONResponse({"result": result})


@callbacks.get("/shopify", include_in_schema=False)
async def shopify_callback(request: Request, db: DbSession) -> Response:
    await _limit("integration-callback", _ip(request), 60)
    connection_id, result = await receiver.shopify_callback(
        db, list(request.query_params.multi_items())
    )
    return _finish(connection_id, result)


@callbacks.post("/woocommerce", include_in_schema=False)
async def woocommerce_callback(request: Request, db: DbSession) -> Response:
    await _limit("integration-callback", _ip(request), 60)
    body = await _body(request)
    status = 413 if body is None else await receiver.woocommerce_callback(db, body)
    return JSONResponse({"received": status == 200}, status_code=status)


@callbacks.get("/meta", include_in_schema=False)
async def meta_callback(request: Request, db: DbSession) -> Response:
    await _limit("integration-callback", _ip(request), 60)
    connection_id, result = await receiver.meta_callback(db, dict(request.query_params))
    return _finish(connection_id, result)


@webhooks.post("/shopify-compliance", include_in_schema=False)
async def shopify_compliance(request: Request, db: DbSession) -> Response:
    await _limit("integration-webhook", "shopify-compliance", 600)
    body = await _body(request)
    if body is None:
        return JSONResponse({"received": False}, status_code=413)
    ack = await receiver.shopify_compliance(db, _headers(request), body)
    return JSONResponse(ack.body, status_code=ack.status)


@webhooks.get("/meta", include_in_schema=False)
async def meta_verify(request: Request) -> Response:
    params = request.query_params
    if not meta.valid_challenge(params.get("hub.mode"), params.get("hub.verify_token")):
        return PlainTextResponse("forbidden", status_code=403)
    return PlainTextResponse(params.get("hub.challenge", ""))


@webhooks.post("/meta", include_in_schema=False)
async def meta_webhook(request: Request, db: DbSession) -> Response:
    await _limit("integration-webhook", "meta", 3000)
    body = await _body(request)
    if body is None:
        return JSONResponse({"received": False}, status_code=413)
    ack = await receiver.meta_event(db, _headers(request), body)
    return JSONResponse(ack.body, status_code=ack.status)


@webhooks.post("/{provider}/{token}", include_in_schema=False)
async def provider_webhook(provider: str, token: str, request: Request, db: DbSession) -> Response:
    name = {"shopify": "SHOPIFY", "woocommerce": "WOOCOMMERCE"}.get(provider)
    if name is None:
        return JSONResponse({"received": False}, status_code=404)
    await _limit("integration-webhook", token[:64], 600)
    body = await _body(request)
    if body is None:
        return JSONResponse({"received": False}, status_code=413)
    ack = await receiver.receive(db, name, token, _headers(request), body)
    return JSONResponse(ack.body, status_code=ack.status)
