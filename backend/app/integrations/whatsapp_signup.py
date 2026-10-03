"""Meta Embedded Signup v4. Provider tokens never leave the encrypted vault.

The one-time launch ticket is carried in a URL fragment, redeemed once for a
browser-only capability, and bound to the initiating seller, shop and row.
The existing connection's state columns serialize bootstrap/completion/replay.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.context import ActorType, use_context
from app.core.errors import ConflictError, ValidationError
from app.db.tenancy import allow_cross_tenant
from app.integrations import http, meta, service, whatsapp
from app.integrations.http import ProviderError
from app.integrations.models import IntegrationConnection
from app.tenants.models import Tenant, TenantUser
from app.tenants.roles import Permission, has_permission
from app.users.models import User

PATH = "/ecomsbd/whatsapp-connect"
TTL = timedelta(minutes=10)
SCOPES = {"whatsapp_business_management", "whatsapp_business_messaging"}


def blocker(user_id: uuid.UUID | None = None) -> str | None:
    settings = get_settings()
    if not (
        meta.configured()
        and settings.meta_whatsapp_embedded_signup_config_id
        and settings.public_web_url
        and settings.public_web_url.startswith("https://")
    ):
        return "META_APP_SETUP_REQUIRED"
    if not settings.meta_whatsapp_public_signup_enabled and (
        user_id not in settings.meta_whatsapp_test_user_ids
    ):
        return "META_APPROVAL_REQUIRED"
    return None


def origin() -> str:
    parsed = urlsplit(get_settings().public_web_url or "")
    return f"{parsed.scheme}://{parsed.netloc}"


def failure(code: str) -> ConflictError:
    return ConflictError("WhatsApp setup could not continue", details={"code": code})


async def start(
    db: AsyncSession, conn: IntegrationConnection, user_id: uuid.UUID
) -> dict[str, Any]:
    reason = blocker(user_id)
    if reason:
        raise ConflictError("WhatsApp setup is unavailable", details={"blocker": reason})
    ticket = service.new_state(conn)
    conn.state_expires_at = utc_now() + TTL
    conn.config = {
        **conn.config,
        "return_to": "app",
        "whatsapp_signup": {"stage": "CREATED", "user_id": str(user_id)},
    }
    await db.flush()
    return {
        "authorize_url": f"{(get_settings().public_web_url or '').rstrip('/')}{PATH}#state={ticket}",
        "expires_at": conn.state_expires_at,
    }


async def resolve(db: AsyncSession, credential: str) -> IntegrationConnection:
    with allow_cross_tenant("WhatsApp signup capability lookup"):
        conn = await db.scalar(
            sa.select(IntegrationConnection)
            .where(
                IntegrationConnection.provider == "WHATSAPP",
                IntegrationConnection.state_hash == service.digest(credential),
            )
            .with_for_update()
        )
    if conn is None or not conn.state_expires_at or conn.state_expires_at <= utc_now():
        raise failure("STATE_INVALID")
    return conn


async def actor(db: AsyncSession, conn: IntegrationConnection) -> uuid.UUID:
    user_id = uuid.UUID(conn.config["whatsapp_signup"]["user_id"])
    member = await db.scalar(sa.select(TenantUser).where(TenantUser.user_id == user_id))
    user = await db.get(User, user_id)
    shop = await db.get(Tenant, conn.tenant_id)
    if (
        not member
        or not member.is_active
        or not has_permission(member.role, Permission.SETTINGS_MANAGE)
        or not user
        or not user.is_active
        or not shop
        or shop.status != "ACTIVE"
        or shop.deleted_at
    ):
        raise failure("STATE_INVALID")
    return user_id


async def bootstrap(db: AsyncSession, signup_state: str) -> dict[str, Any]:
    conn = await resolve(db, signup_state)
    with use_context(tenant_id=conn.tenant_id, actor_type=ActorType.PROVIDER):
        user_id = await actor(db, conn)
        if conn.config["whatsapp_signup"]["stage"] != "CREATED":
            raise failure("STATE_REUSED")
        reason = blocker(user_id)
        if reason:
            raise failure(reason)
        browser_session = secrets.token_urlsafe(32)
        conn.state_hash = service.digest(browser_session)
        conn.config = {
            **conn.config,
            "whatsapp_signup": {"stage": "OPEN", "user_id": str(user_id)},
        }
        await db.flush()
        settings = get_settings()
        return {
            "browser_session": browser_session,
            "connection_id": str(conn.id),
            "app_id": settings.meta_app_id,
            "config_id": settings.meta_whatsapp_embedded_signup_config_id,
            "graph_version": settings.meta_graph_api_version,
        }


async def graph(path: str, token: str, **params: Any) -> dict[str, Any]:
    reply = await http.send(
        "GET", f"{meta._graph()}{path}", params=params, headers=whatsapp._auth(token)
    )
    data = meta._checked(reply)
    if not isinstance(data, dict):
        raise ProviderError("MALFORMED_RESPONSE")
    return data


async def exchange(authorization_code: str) -> str:
    # This is the business-token exchange, NOT Messenger's user-token upgrade.
    reply = await http.send(
        "GET",
        f"{meta._graph()}/oauth/access_token",
        params={
            "client_id": get_settings().meta_app_id,
            "client_secret": meta._secret(),
            "code": authorization_code,
        },
    )
    data = meta._checked(reply)
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise ProviderError("MALFORMED_RESPONSE")
    return token


async def assets(token: str, waba_id: str | None, phone_id: str | None) -> tuple[str, str]:
    settings = get_settings()
    debug = (
        await graph("/debug_token", f"{settings.meta_app_id}|{meta._secret()}", input_token=token)
    ).get("data") or {}
    if not debug.get("is_valid") or str(debug.get("app_id")) != settings.meta_app_id:
        raise ProviderError("AUTH_EXPIRED")
    now = int(utc_now().timestamp())
    if any(
        debug.get(key) and int(debug[key]) <= now
        for key in ("expires_at", "data_access_expires_at")
    ):
        raise ProviderError("AUTH_EXPIRED")
    if not SCOPES.issubset(set(debug.get("scopes") or [])):
        raise ProviderError("PERMISSION_MISSING")
    grants = {
        g["scope"]: set(map(str, g.get("target_ids") or []))
        for g in debug.get("granular_scopes") or []
        if isinstance(g, dict) and "scope" in g
    }
    allowed = grants.get("whatsapp_business_management", set()) & grants.get(
        "whatsapp_business_messaging", set()
    )
    if waba_id is None and len(allowed) == 1:
        waba_id = next(iter(allowed))
    if not waba_id or waba_id not in allowed:
        raise ProviderError("WABA_MISMATCH")
    account = await graph(f"/{waba_id}", token, fields="id,name")
    if str(account.get("id")) != waba_id:
        raise ProviderError("WABA_MISMATCH")
    numbers: set[str] = set()
    after = None
    for _ in range(20):
        page = await graph(
            f"/{waba_id}/phone_numbers",
            token,
            fields="id",
            limit=100,
            **({"after": after} if after else {}),
        )
        numbers.update(str(row["id"]) for row in page.get("data") or [] if row.get("id"))
        paging = page.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after")
        if not after or not paging.get("next"):
            break
    if phone_id is None and len(numbers) == 1:
        phone_id = next(iter(numbers))
    if not phone_id or phone_id not in numbers:
        raise ProviderError("PHONE_NUMBER_MISMATCH")
    return waba_id, phone_id


async def finish(
    db: AsyncSession,
    *,
    browser_session: str,
    connection_id: uuid.UUID,
    authorization_code: str | None,
    waba_id: str | None,
    phone_number_id: str | None,
    cancelled: bool = False,
) -> dict[str, Any]:
    conn = await resolve(db, browser_session)
    if conn.id != connection_id:
        raise failure("STATE_INVALID")
    with use_context(tenant_id=conn.tenant_id, actor_type=ActorType.PROVIDER):
        user_id = await actor(db, conn)
        attempt = conn.config["whatsapp_signup"]
        fingerprint = service.digest(
            f"{authorization_code}|{waba_id}|{phone_number_id}|{cancelled}"
        )
        if attempt["stage"] != "OPEN":
            if attempt.get("fingerprint") != fingerprint:
                raise failure("STATE_REUSED")
            return result(conn, attempt["result"])
        reason = blocker(user_id)
        if reason:
            raise failure(reason)
        # Claim and commit before any provider I/O: a crash cannot exchange a
        # code twice. An interrupted completion must start a new signup.
        conn.config = {
            **conn.config,
            "whatsapp_signup": {
                **attempt,
                "stage": "PROCESSING",
                "fingerprint": fingerprint,
                "result": "SIGNUP_IN_PROGRESS",
            },
        }
        await db.flush()
        await db.commit()
        await db.refresh(conn, with_for_update=True)
        if (
            conn.state_hash != service.digest(browser_session)
            or conn.config["whatsapp_signup"]["stage"] != "PROCESSING"
        ):
            raise failure("STATE_INVALID")
        outcome = "ACCESS_DENIED" if cancelled else "CONNECTED"
        try:
            if not cancelled:
                if not authorization_code:
                    raise ProviderError("SIGNUP_INCOMPLETE")
                token = await exchange(authorization_code)
                waba, phone = await assets(token, waba_id, phone_number_id)
                await service.ensure_unlinked(db, conn, phone)
                info = await graph(f"/{phone}", token, fields="id,status,code_verification_status")
                if (
                    str(info.get("id")) != phone
                    or info.get("code_verification_status") != "VERIFIED"
                ):
                    raise ProviderError("PHONE_NOT_VERIFIED")
                credentials = service.unseal(conn)
                pin = credentials.get("registration_pin") or f"{secrets.randbelow(1000000):06d}"
                if info.get("status") != "CONNECTED":
                    service.seal(conn, {**credentials, "registration_pin": pin})
                    reply = await http.send(
                        "POST",
                        f"{meta._graph()}/{phone}/register",
                        headers=whatsapp._auth(token),
                        json_body={"messaging_product": "whatsapp", "pin": pin},
                    )
                    if not (meta._checked(reply) or {}).get("success"):
                        raise ProviderError("PHONE_REGISTRATION_REQUIRED")
                    registered = await graph(f"/{phone}", token, fields="id,status")
                    if (
                        str(registered.get("id")) != phone
                        or registered.get("status") != "CONNECTED"
                    ):
                        raise ProviderError("PHONE_REGISTRATION_REQUIRED")
                # Reuse the lifecycle; strict webhook verification is required.
                await service.connect_whatsapp(
                    db,
                    conn,
                    phone_number_id=phone,
                    waba_id=waba,
                    access_token=token,
                    require_webhook=True,
                )
                if info.get("status") != "CONNECTED" or credentials.get("registration_pin"):
                    service.seal(conn, {**service.unseal(conn), "registration_pin": pin})
        except ProviderError as exc:
            outcome = exc.code
            service.mark_error(conn, outcome)
        except ConflictError:
            outcome = "ALREADY_LINKED"
        except ValidationError:
            outcome = "ACCOUNT_MISMATCH"
        conn.config = {
            **conn.config,
            "whatsapp_signup": {
                **conn.config["whatsapp_signup"],
                "stage": "FINISHED",
                "result": outcome,
            },
        }
        await db.flush()
        return result(conn, outcome)


def result(conn: IntegrationConnection, outcome: str) -> dict[str, Any]:
    return {
        "result": outcome,
        "connection_id": str(conn.id),
        "return_url": service.return_url(conn, outcome),
    }
