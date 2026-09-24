"""Outbound provider HTTP with seller-safe failures.

A WooCommerce store URL is typed by a seller, so every request goes through the
same guard as outbound webhooks: HTTPS on 443, a public hostname, DNS pinned to
the address that was checked, no proxies and no redirects. Shopify and Meta
hosts pass the same guard.

Failures become :class:`ProviderError` codes. The provider's own message is
never kept: it can echo tokens, and a seller cannot act on it anyway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.errors import ValidationError
from app.public_api.webhooks import resolve_public, valid_url

#: Codes a later attempt can clear without anyone changing anything.
TRANSIENT = frozenset({"PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED"})
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class ProviderError(Exception):
    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status

    @property
    def transient(self) -> bool:
        return self.code in TRANSIENT


@dataclass(frozen=True)
class Reply:
    status: int
    data: Any
    headers: dict[str, str]


async def send(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    auth: tuple[str, str] | None = None,
    read_seconds: float = 20.0,
) -> Reply:
    """One request to a public HTTPS host. Tests replace this function."""
    try:
        host = valid_url(url)
        ip = await resolve_public(host)
    except (ValidationError, OSError, TimeoutError) as exc:
        raise ProviderError("STORE_UNREACHABLE") from exc
    target = httpx.URL(url, params=params).copy_with(host=ip)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(read_seconds, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.request(
                method,
                target,
                json=json_body,
                auth=auth,
                headers={**(headers or {}), "Host": host, "Accept": "application/json"},
                extensions={"sni_hostname": host},
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("PROVIDER_TIMEOUT") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise ProviderError("PROVIDER_UNAVAILABLE") from exc
    body = response.content[:MAX_RESPONSE_BYTES]
    try:
        data = json.loads(body) if body else None
    except ValueError:
        data = None
    return Reply(response.status_code, data, {k.lower(): v for k, v in response.headers.items()})


def raise_for_status(reply: Reply) -> Reply:
    status = reply.status
    if 200 <= status < 300:
        return reply
    if status == 401:
        raise ProviderError("AUTH_EXPIRED", status=status)
    if status == 403:
        raise ProviderError("PERMISSION_MISSING", status=status)
    if status == 404:
        raise ProviderError("NOT_FOUND_AT_PROVIDER", status=status)
    if status == 429:
        raise ProviderError("PROVIDER_RATE_LIMITED", status=status)
    if status >= 500:
        raise ProviderError("PROVIDER_UNAVAILABLE", status=status)
    if 300 <= status < 400:
        # A store that redirects its API (http->https, www) is misconfigured
        # for us; following it would bypass the host check.
        raise ProviderError("STORE_REDIRECTED", status=status)
    raise ProviderError("PROVIDER_REJECTED", status=status)
