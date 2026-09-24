"""Messenger Page connections, from Meta's developer documentation only.

Facebook Login returns a user token; ``/me/accounts`` lists the Pages it can
manage with a Page token each; ``POST /{page-id}/subscribed_apps`` subscribes
the app to the Page's ``messages`` field. Webhook deliveries are signed with
``X-Hub-Signature-256: sha256=<hex HMAC of the raw body under the app secret>``
and the subscription is verified with the ``hub.*`` challenge.

This is connection health only. The V2 messaging domain sends transactional
email; it has no Messenger conversation channel to link, so message contents
are neither stored nor answered.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from urllib.parse import urlencode

from app.core.config import get_settings
from app.integrations import http
from app.integrations.http import ProviderError

SCOPES = "pages_show_list,pages_manage_metadata,pages_messaging"


def configured() -> bool:
    settings = get_settings()
    return bool(
        settings.meta_app_id and settings.meta_app_secret and settings.meta_webhook_verify_token
    )


def _graph() -> str:
    return f"https://graph.facebook.com/{get_settings().meta_graph_api_version}"


def _secret() -> str:
    secret = get_settings().meta_app_secret
    if secret is None:
        raise ProviderError("META_APP_SETUP_REQUIRED")
    return secret.get_secret_value()


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + "/v1/integration-callbacks/meta"


def authorize_url(state: str) -> str:
    settings = get_settings()
    query = urlencode(
        {
            "client_id": settings.meta_app_id,
            "redirect_uri": redirect_uri(),
            "state": state,
            "scope": SCOPES,
            "response_type": "code",
        }
    )
    return f"https://www.facebook.com/{settings.meta_graph_api_version}/dialog/oauth?{query}"


def valid_challenge(mode: str | None, token: str | None) -> bool:
    expected = get_settings().meta_webhook_verify_token
    return (
        mode == "subscribe"
        and expected is not None
        and token is not None
        and hmac.compare_digest(token, expected.get_secret_value())
    )


def valid_webhook(body: bytes, header: str | None) -> bool:
    if not header or not configured():
        return False
    digest = hmac.new(_secret().encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + digest, header.strip())


async def _get(path: str, params: dict[str, Any]) -> Any:
    reply = await http.send("GET", _graph() + path, params=params)
    return _checked(reply)


def _checked(reply: http.Reply) -> Any:
    if reply.status >= 400 and isinstance(reply.data, dict):
        code = (reply.data.get("error") or {}).get("code")
        if code == 190:
            raise ProviderError("AUTH_EXPIRED", status=reply.status)
        if code == 10 or (isinstance(code, int) and 200 <= code < 300):
            raise ProviderError("PERMISSION_MISSING", status=reply.status)
    return http.raise_for_status(reply).data


async def exchange_code(code: str) -> str:
    settings = get_settings()
    short = await _get(
        "/oauth/access_token",
        {
            "client_id": settings.meta_app_id,
            "redirect_uri": redirect_uri(),
            "client_secret": _secret(),
            "code": code,
        },
    )
    long = await _get(
        "/oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": settings.meta_app_id,
            "client_secret": _secret(),
            "fb_exchange_token": (short or {}).get("access_token", ""),
        },
    )
    token = (long or {}).get("access_token")
    if not token:
        raise ProviderError("MALFORMED_RESPONSE")
    return token


async def pages(user_token: str) -> list[dict[str, str]]:
    data = await _get(
        "/me/accounts", {"fields": "id,name,access_token", "limit": 50, "access_token": user_token}
    )
    return [
        {"id": str(row["id"]), "name": str(row.get("name") or ""), "token": row["access_token"]}
        for row in (data or {}).get("data") or []
        if row.get("id") and row.get("access_token")
    ]


async def subscribe(page_id: str, page_token: str) -> None:
    reply = await http.send(
        "POST",
        f"{_graph()}/{page_id}/subscribed_apps",
        params={"subscribed_fields": "messages", "access_token": page_token},
    )
    if not (_checked(reply) or {}).get("success"):
        raise ProviderError("WEBHOOK_REGISTRATION_FAILED")


async def check(page_id: str, page_token: str) -> dict[str, Any]:
    data = await _get(f"/{page_id}", {"fields": "id,name", "access_token": page_token})
    if str((data or {}).get("id")) != page_id:
        raise ProviderError("ACCOUNT_MISMATCH")
    return data
