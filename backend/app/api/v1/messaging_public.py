"""Unauthenticated messaging traffic: unsubscribe links and provider receipts.

The unsubscribe link needs no sign-in by design (RFC 8058 one-click). A GET
only shows a confirmation, so mail scanners that prefetch links unsubscribe
nobody; the POST, from the page's button or a mail client's one-click header,
records the opt-out. Neither response says whether a token was valid.
"""

from __future__ import annotations

import html
import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.deps import DbSession, get_hasher
from app.api.v1.integration_webhooks import _body, _headers, _limit
from app.messaging import receipts

public = APIRouter(prefix="/messaging", tags=["messaging"])
webhooks = APIRouter(prefix="/webhooks/messaging", tags=["messaging"])

_PAGE = """<!doctype html><html lang="bn"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem;
line-height:1.5}}button{{font-size:1rem;padding:.6rem 1.2rem}}</style></head><body>{body}</body></html>"""


def _page(title: str, body: str) -> HTMLResponse:
    response = HTMLResponse(_PAGE.format(title=html.escape(title), body=body))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _ip(request: Request) -> str:
    return get_hasher().ip_hash(request.client.host if request.client else "unknown") or "unknown"


@public.get("/unsubscribe/{token}", include_in_schema=False)
async def confirm(token: str, request: Request) -> Response:
    await _limit("messaging-unsubscribe", _ip(request), 60)
    action = html.escape(f"{request.url.path}")
    return _page(
        "Unsubscribe",
        "<h1>অফার মেসেজ বন্ধ করুন · Stop offers</h1>"
        "<p>এই ঠিকানায় আর অফার/মার্কেটিং মেসেজ পাঠানো হবে না। অর্ডারের আপডেট আগের মতো আসবে।</p>"
        "<p>You will no longer receive offers at this address. Order updates continue.</p>"
        f'<form method="post" action="{action}"><button type="submit">'
        "বন্ধ করুন · Unsubscribe</button></form>",
    )


@public.post("/unsubscribe/{token}", include_in_schema=False)
async def unsubscribe(token: str, request: Request, db: DbSession) -> Response:
    await _limit("messaging-unsubscribe", _ip(request), 60)
    await receipts.unsubscribe(db, token)
    return _page(
        "Unsubscribed",
        "<h1>সম্পন্ন · Done</h1><p>আপনাকে আর অফার মেসেজ পাঠানো হবে না।</p>"
        "<p>You will not receive further offers.</p>",
    )


@webhooks.post("/resend", include_in_schema=False)
async def resend(request: Request, db: DbSession) -> Response:
    await _limit("messaging-webhook", "resend", 3000)
    body = await _body(request)
    if body is None:
        return JSONResponse({"received": False}, status_code=413)
    if not receipts.valid_svix(_headers(request), body):
        return JSONResponse({"received": False}, status_code=401)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"received": True})
    if isinstance(payload, dict):
        await receipts.resend_event(db, payload)
    return JSONResponse({"received": True})
