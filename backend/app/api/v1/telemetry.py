"""Opt-in app telemetry relay. Server credentials and verified billing stay here."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.billing.models import BillingTransaction
from app.common.cache import RateLimiter, get_cache
from app.core.clock import utc_now

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
APP_ID = "com.ecomsbd.app"


def identity(principal) -> str:
    return f"{principal.user_id}:{principal.require_tenant()}"


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analytics: StrictBool
    account: str | None = None


@router.get("/consent")
async def get_consent(principal: CurrentPrincipal):
    return {"analytics": await get_cache().get(f"fg:consent:{identity(principal)}") == "true"}


@router.post("/consent")
async def set_consent(body: Consent, principal: CurrentPrincipal):
    if body.account is not None and body.account != identity(principal):
        raise HTTPException(409, "Account changed; reload consent")
    await get_cache().set(
        f"fg:consent:{identity(principal)}", "true" if body.analytics else "false"
    )
    return {"analytics": body.analytics}


def bound_payload(body: dict, account: str) -> tuple[str, dict]:
    if body.get("accountBinding") != account:
        raise HTTPException(409, "Account changed; retry after signing in")
    kind = body.get("kind")
    path = {
        "install": "app-installs",
        "event": "app-events",
        "apple_attribution": "apple-ads-attribution",
    }.get(kind)
    if not path or body.get("appId") != APP_ID or body.get("consent") != {"analytics": True}:
        raise HTTPException(422, "Invalid app telemetry")
    install_id = body.get("installId")
    if not isinstance(install_id, str) or not 8 <= len(install_id) <= 200:
        raise HTTPException(422, "Invalid installation identity")
    if kind != "apple_attribution" and body.get("platform") not in ("android", "ios"):
        raise HTTPException(422, "Invalid platform")
    payload = {k: v for k, v in body.items() if k not in ("kind", "userId", "accountBinding")}
    # One installation belongs to the authenticated seller/shop, never a client-provided user.
    payload["installId"] = hashlib.sha256(f"{account}:{install_id}".encode()).hexdigest()
    return path, payload


@router.get("/conversions")
async def conversions(principal: CurrentPrincipal, db: DbSession):
    if str(principal.role) != "OWNER" or not (await get_consent(principal))["analytics"]:
        return {"transactions": []}
    install = await get_cache().get(f"fg:install:{identity(principal)}")
    if not install:
        return {"transactions": []}
    installed_at = datetime.fromisoformat(json.loads(install)["installedAt"].replace("Z", "+00:00"))
    rows = (
        await db.scalars(
            sa.select(BillingTransaction)
            .where(
                BillingTransaction.tenant_id == principal.require_tenant(),
                BillingTransaction.state == "VERIFIED",
                BillingTransaction.kind.in_(["PURCHASE", "RENEWAL"]),
                BillingTransaction.occurred_at >= utc_now() - timedelta(days=30),
                BillingTransaction.occurred_at >= installed_at,
            )
            .order_by(BillingTransaction.occurred_at)
            .limit(100)
        )
    ).all()
    return {
        "transactions": [
            {"id": str(r.id), "valueMicros": r.amount_paisa * 10000, "currency": r.currency}
            for r in rows
        ]
    }


@router.post("")
async def relay(
    request: Request, principal: CurrentPrincipal, settings: SettingsDep, db: DbSession
):
    account = identity(principal)
    rate = await RateLimiter().hit("futuregrow", account, limit=60, window_seconds=60)
    if not rate.allowed:
        raise HTTPException(429, "Telemetry rate limit")
    if not (await get_consent(principal))["analytics"]:
        raise HTTPException(403, "Analytics consent denied")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 20000:
            raise HTTPException(413, "Telemetry payload too large")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid telemetry JSON") from None
    path, payload = bound_payload(body, account)
    if body.get("event") == "activation":
        # Activation is completed seller onboarding: an authenticated shop membership.
        principal.require_tenant()
    if body.get("event") == "conversion":
        try:
            transaction_id = uuid.UUID(str(body.get("eventId", "")).removeprefix("conversion_"))
        except ValueError:
            raise HTTPException(422, "Invalid billing identity") from None
        transaction = await db.scalar(
            sa.select(BillingTransaction)
            .where(
                BillingTransaction.id == transaction_id,
                BillingTransaction.tenant_id == principal.require_tenant(),
            )
            .with_for_update()
        )
        expected = (
            {
                "name": "subscription_paid",
                "valueMicros": transaction.amount_paisa * 10000,
                "currency": transaction.currency,
            }
            if transaction
            else None
        )
        if (
            str(principal.role) != "OWNER"
            or not transaction
            or transaction.state != "VERIFIED"
            or transaction.kind not in ("PURCHASE", "RENEWAL")
            or body.get("conversion") != expected
        ):
            raise HTTPException(422, "Unverified subscription conversion")
        recorded_install = await get_cache().get(f"fg:install:{account}")
        if not recorded_install:
            raise HTTPException(422, "Record installation before subscription conversion")
        install = json.loads(recorded_install)
        installed_at = datetime.fromisoformat(install["installedAt"].replace("Z", "+00:00"))
        if install["installId"] != payload["installId"] or transaction.occurred_at < installed_at:
            return {"recorded": False, "outsideInstallation": True}
        # Provider-verified identity/time prevent alternative spellings or retries counting twice.
        payload["eventId"] = f"conversion_{transaction.id}"
        payload["occurredAt"] = transaction.occurred_at.astimezone(UTC).isoformat()
        bound = transaction.metadata_json.get("futuregrow_install")
        if bound and bound != payload["installId"]:
            # Already assigned to another installation: acknowledge without double counting.
            return {"recorded": False, "duplicate": True}
        transaction.metadata_json = {
            **transaction.metadata_json,
            "futuregrow_install": payload["installId"],
        }
        await db.commit()
    base = settings.futuregrow_base_url
    parsed = urlparse(base or "")
    key = (
        settings.futuregrow_server_key.get_secret_value() if settings.futuregrow_server_key else ""
    )
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not key.startswith("fgk_sk_")
    ):
        raise HTTPException(503, "Telemetry relay is not configured")
    # Recheck after asynchronous verification; a withdrawn consent stops future sends.
    if not (await get_consent(principal))["analytics"]:
        raise HTTPException(403, "Analytics consent denied")
    try:
        async with (
            httpx.AsyncClient(timeout=45, follow_redirects=False) as client,
            client.stream(
                "POST",
                f"{base.rstrip('/')}/ingest/v1/{path}",
                headers={"authorization": f"Bearer {key}"},
                json=payload,
            ) as upstream,
        ):
            if 300 <= upstream.status_code < 400:
                raise HTTPException(502, "Upstream redirect refused")
            data = bytearray()
            async for chunk in upstream.aiter_bytes():
                data.extend(chunk)
                if len(data) > 64000:
                    raise HTTPException(502, "Upstream response too large")
            if path == "app-installs" and 200 <= upstream.status_code < 300:
                ack = json.loads(data)
                if ack.get("recorded") is True or (
                    ack.get("recorded") is False and not ack.get("reason")
                ):
                    await get_cache().set(
                        f"fg:install:{account}",
                        json.dumps(
                            {
                                "installId": payload["installId"],
                                "installedAt": payload["installedAt"],
                            }
                        ),
                    )
            return Response(
                bytes(data), status_code=upstream.status_code, media_type="application/json"
            )
    except httpx.HTTPError:
        raise HTTPException(503, "Telemetry delivery deferred") from None
