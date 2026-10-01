"""Unauthenticated integration traffic: OAuth callbacks and provider webhooks.

Kept under their own prefixes so no seller route with a path parameter can
shadow them. Every route is rate limited before it reads the body, and none of
them says whether a token or a shop exists.
"""

from __future__ import annotations

import html
import re
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from app.api.deps import DbSession, get_hasher
from app.common.cache import RateLimiter
from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.db.tenancy import allow_cross_tenant
from app.integrations import meta, receiver
from app.integrations.models import IntegrationConnection

#: The mobile app's registered URL scheme (Android intent filter, iOS
#: CFBundleURLSchemes); the same one Supabase sign-in returns on.
APP_SCHEME = "com.ecomsbd.app"
_RESULT = re.compile(r"^[A-Za-z_]{1,40}$")

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


async def _returns_to_app(db: DbSession, connection_id: object) -> bool:
    if not isinstance(connection_id, uuid.UUID):
        return False
    with allow_cross_tenant("integration callback: return target"):
        config = await db.scalar(
            sa.select(IntegrationConnection.config).where(IntegrationConnection.id == connection_id)
        )
    return isinstance(config, dict) and config.get("return_to") == "app"


async def _finish(db: DbSession, connection_id: object, result: str) -> Response:
    if connection_id is not None and await _returns_to_app(db, connection_id):
        return _return_page(str(connection_id), result)
    web = (get_settings().public_web_url or "").rstrip("/")
    if web and connection_id is not None:
        return RedirectResponse(f"{web}/integrations/{connection_id}?result={result}", 303)
    return JSONResponse({"result": result})


def _return_page(connection_id: str, result: str) -> HTMLResponse:
    """Hand the seller back to the app after a provider's sign-in page.

    Says nothing about the shop or the outcome beyond a code the app already
    knows: the app asks the API what happened.
    """
    link = html.escape(
        f"{APP_SCHEME}://integrations/return?connection={connection_id}&result={result}",
        quote=True,
    )
    page = f"""<!doctype html>
<html lang="bn"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>ecomsbd</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;padding:48px 24px;text-align:center;color:#0f1d2e}}
a{{display:inline-block;margin-top:24px;padding:14px 28px;border-radius:14px;background:#f26b1d;color:#fff;
text-decoration:none;font-weight:700}}p{{color:#5b6b7c}}</style></head>
<body><h2>ecomsbd অ্যাপে ফিরে যান</h2><p>Return to the ecomsbd app to finish connecting.</p>
<a href="{link}">ecomsbd খুলুন · Open ecomsbd</a>
<script>window.location.replace("{link}");</script></body></html>"""
    return HTMLResponse(
        page,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@callbacks.get("/shopify", include_in_schema=False)
async def shopify_callback(request: Request, db: DbSession) -> Response:
    await _limit("integration-callback", _ip(request), 60)
    connection_id, result = await receiver.shopify_callback(
        db, list(request.query_params.multi_items())
    )
    return await _finish(db, connection_id, result)


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
    return await _finish(db, connection_id, result)


@callbacks.get("/return", include_in_schema=False)
async def app_return(request: Request) -> Response:
    """Where a provider's sign-in sends a seller who started in the app.

    WooCommerce appends ``success=0`` when the seller declines; anything else
    is only a hint, and the app checks the connection itself.
    """
    await _limit("integration-callback", _ip(request), 60)
    params = request.query_params
    try:
        connection_id = str(uuid.UUID(params.get("connection", "")))
    except ValueError:
        return JSONResponse({"result": "STATE_INVALID"}, status_code=400)
    result = params.get("result", "")
    if not _RESULT.match(result):
        result = "UNKNOWN"
    if params.get("success") == "0":
        result = "ACCESS_DENIED"
    return _return_page(connection_id, result)


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
