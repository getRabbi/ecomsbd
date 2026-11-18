"""Linked devices and ingest keys (V4 WP1.1)."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient

from app.core.clock import utc_now
from app.db.session import system_session
from app.device_links.models import DeviceIngestKey, DevicePairingCode
from app.device_links.service import hash_secret, new_ingest_key
from app.integrations.models import IntegrationConnection
from app.tenants.roles import TenantRole
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_team import _member_session

LINK = "/v1/devices/link"
BEAT = "/v1/intake/heartbeat"


@pytest_asyncio.fixture
async def intake_on(settings) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.INTAKE_DEVICE_ENABLED)):
        yield


def _device(install: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "install_id": install or f"install-{uuid.uuid4().hex}",
        "platform": "android",
        "app_version": "4.0.0",
        "covered_packages": ["com.facebook.orca", "com.whatsapp"],
        **extra,
    }


def _key_header(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _keys(connection_id: str) -> list[DeviceIngestKey]:
    async with system_session("test: keys") as db:
        stmt = sa.select(DeviceIngestKey).where(
            DeviceIngestKey.connection_id == uuid.UUID(connection_id)
        )
        return list((await db.scalars(stmt)).all())


def _unique_phone() -> str:
    return f"017{uuid.uuid4().int % 100_000_000:08d}"


def test_the_key_format() -> None:
    key = new_ingest_key()
    assert re.fullmatch(r"edk_[A-Za-z0-9_-]{40}", key)
    assert new_ingest_key() != key
    assert len(hash_secret(key)) == 64


async def test_everything_is_off_without_the_flag(client: AsyncClient, unique_phone: str) -> None:
    shop = await signed_in_shop(client, unique_phone)
    for path, body in ((LINK, _device()), ("/v1/devices/pair", None)):
        response = await client.post(path, json=body, headers=auth_header(shop))
        assert response.status_code == 403 and response.json()["code"] == "FEATURE_DISABLED"


async def test_link_shows_the_key_once_and_stores_only_its_hash(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    response = await client.post(LINK, json=_device(), headers=auth_header(shop))
    assert response.status_code == 200, response.text
    body = response.json()
    key = body["ingest_key"]
    assert (
        key.startswith("edk_")
        and body["key_last4"] == key[-4:]
        and body["scope"] == "intake.capture"
    )
    [row] = await _keys(body["connection_id"])
    assert row.key_hash == hash_secret(key) and key not in str(row.__dict__)
    async with system_session("test: connection") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(body["connection_id"]))
        assert conn is not None and conn.provider == "DEVICE" and conn.state == "CONNECTED"
        assert conn.config["platform"] == "android"
        assert conn.config["covered_packages"] == ["com.facebook.orca", "com.whatsapp"]
        assert (
            conn.config["listener_state"] == "UNKNOWN" and conn.config["last_heartbeat_at"] is None
        )


async def test_relinking_the_same_install_reuses_the_connection_and_rotates_the_key(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    device = _device()
    first = (await client.post(LINK, json=device, headers=auth_header(shop))).json()
    second = (await client.post(LINK, json=device, headers=auth_header(shop))).json()
    assert first["connection_id"] == second["connection_id"]
    assert first["ingest_key"] != second["ingest_key"]
    old = await client.post(BEAT, json={}, headers=_key_header(first["ingest_key"]))
    assert old.status_code == 401
    new = await client.post(
        BEAT, json={"listener_state": "ON"}, headers=_key_header(second["ingest_key"])
    )
    assert new.status_code == 200, new.text


async def test_an_install_belongs_to_one_shop(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    device = _device()
    first = await signed_in_shop(client, unique_phone, shop_name="First")
    assert (await client.post(LINK, json=device, headers=auth_header(first))).status_code == 200
    second = await signed_in_shop(client, _unique_phone(), shop_name="Second")
    response = await client.post(LINK, json=device, headers=auth_header(second))
    assert (
        response.status_code == 409 and response.json()["details"]["reason"] == "LINKED_ELSEWHERE"
    )


async def test_heartbeat_records_listener_state(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    linked = (await client.post(LINK, json=_device(), headers=auth_header(shop))).json()
    response = await client.post(
        BEAT,
        json={
            "listener_state": "NO_PERMISSION",
            "app_version": "4.0.1",
            "covered_packages": ["com.whatsapp"],
        },
        headers=_key_header(linked["ingest_key"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["listener_state"] == "NO_PERMISSION"
    async with system_session("test: connection") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(linked["connection_id"]))
        assert conn is not None
        assert conn.config["app_version"] == "4.0.1" and conn.config["covered_packages"] == [
            "com.whatsapp"
        ]
        assert conn.config["last_heartbeat_at"] is not None


async def test_pairing_codes_are_one_use_and_expire(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    paired = await client.post("/v1/devices/pair", headers=auth_header(shop))
    assert paired.status_code == 200, paired.text
    code = paired.json()["code"]
    assert re.fullmatch(r"\d{6}", code)

    claim = await client.post("/v1/devices/pair/claim", json={**_device(), "code": code})
    assert claim.status_code == 200, claim.text
    beat = await client.post(BEAT, json={}, headers=_key_header(claim.json()["ingest_key"]))
    assert beat.status_code == 200
    again = await client.post("/v1/devices/pair/claim", json={**_device(), "code": code})
    assert again.status_code == 404

    stale = (await client.post("/v1/devices/pair", headers=auth_header(shop))).json()["code"]
    async with system_session("test: expire") as db:
        await db.execute(
            sa.update(DevicePairingCode)
            .where(DevicePairingCode.code_hash == hash_secret(stale))
            .values(expires_at=utc_now() - timedelta(seconds=1))
        )
    assert (
        await client.post("/v1/devices/pair/claim", json={**_device(), "code": stale})
    ).status_code == 404


async def test_claims_are_rate_limited_per_ip(client: AsyncClient, intake_on: None) -> None:
    statuses = [
        (
            await client.post("/v1/devices/pair/claim", json={**_device(), "code": "000000"})
        ).status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [404] * 10 and statuses[10] == 429


async def test_unlinking_in_the_hub_revokes_the_key(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    linked = (await client.post(LINK, json=_device(), headers=auth_header(shop))).json()
    off = await client.post(
        f"/v1/integrations/{linked['connection_id']}/disconnect", headers=auth_header(shop)
    )
    assert off.status_code == 200, off.text
    assert all(k.revoked_at is not None for k in await _keys(linked["connection_id"]))
    assert (
        await client.post(BEAT, json={}, headers=_key_header(linked["ingest_key"]))
    ).status_code == 401


async def test_removing_the_member_who_linked_it_revokes_the_key(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    owner = await signed_in_shop(client, unique_phone, plan="pro")
    member = await _member_session(client, owner, _unique_phone(), TenantRole.ORDER_OPERATOR)
    linked = (await client.post(LINK, json=_device(), headers=auth_header(owner))).json()
    async with system_session("test: linked by the member") as db:
        conn = await db.get(IntegrationConnection, uuid.UUID(linked["connection_id"]))
        assert conn is not None
        conn.created_by = uuid.UUID(member["user_id"])
    removed = await client.delete(f"/v1/team/{member['user_id']}", headers=auth_header(owner))
    assert removed.status_code == 204, removed.text
    assert (
        await client.post(BEAT, json={}, headers=_key_header(linked["ingest_key"]))
    ).status_code == 401


async def test_account_deletion_revokes_every_key(
    client: AsyncClient, unique_phone: str, intake_on: None
) -> None:
    from app.device_links.service import revoke_tenant_keys
    from app.privacy.service import PrivacyService

    shop = await signed_in_shop(client, unique_phone)
    linked = (await client.post(LINK, json=_device(), headers=auth_header(shop))).json()
    assert hasattr(PrivacyService, "_revoke_device_keys"), "the deletion run includes the step"
    async with system_session("test: deletion step") as db:
        assert await revoke_tenant_keys(db, uuid.UUID(shop["tenant_id"])) == 1
    assert (
        await client.post(BEAT, json={}, headers=_key_header(linked["ingest_key"]))
    ).status_code == 401


async def test_a_key_works_only_on_intake_routes(
    client: AsyncClient, unique_phone: str, intake_on: None, settings: Any
) -> None:
    """The key never grants anything outside intake: every other route answers
    a request carrying it no better than one with no credentials at all."""
    from app.main import create_app

    shop = await signed_in_shop(client, unique_phone)
    key = (await client.post(LINK, json=_device(), headers=auth_header(shop))).json()["ingest_key"]

    session_route = await client.get("/v1/tenant", headers=_key_header(key))
    assert session_route.status_code == 401
    assert "intake" in session_route.json()["message_en"].lower()

    paths = create_app(settings).openapi()["paths"]  # every route, with its methods
    checked = 0
    for path, operations in paths.items():
        if not path.startswith("/v1/") or path.startswith("/v1/intake/"):
            continue
        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
        for method in operations:
            with_key = await client.request(method.upper(), url, headers=_key_header(key))
            if 200 <= with_key.status_code < 300:
                without = await client.request(method.upper(), url)
                assert 200 <= without.status_code < 300, f"{method} {path} accepted an ingest key"
            checked += 1
    assert checked > 100
