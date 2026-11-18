"""Intake rules config (V4 WP1.2): schema, seed, device fetch, shop overrides, admin."""

from __future__ import annotations

import copy
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient
from pydantic import ValidationError

from app.admin.models import PlatformAdminRole
from app.common.audit import AuditAction, AuditLog
from app.common.phone import extract_phones, normalize_digits
from app.db.session import system_session
from app.intake import rules
from app.intake.models import IntakeRuleSet
from app.intake.rules_schema import IntakeRulesV1
from app.orders.parser.districts import DISTRICTS
from app.tenants.roles import TenantRole
from tests.conftest_commerce import signed_in_shop
from tests.test_admin import _provision_admin, admin_client, admin_header  # noqa: F401 - fixture
from tests.test_auth_flow import auth_header
from tests.test_device_links import LINK, _device, _key_header, _unique_phone
from tests.test_team import _member_session

RULES = "/v1/intake/rules"
SETTINGS = "/v1/intake/settings"
ADMIN = "/v1/admin/intake-rules"
BUSINESS_SUITE = "com.facebook.pages.app"
WA_BUSINESS = "com.whatsapp.w4b"
WHATSAPP = "com.whatsapp"
MESSENGER = "com.facebook.orca"


async def _seed() -> dict[str, Any]:
    async with system_session("test: intake rules seed") as db:
        row = (
            await db.execute(sa.select(IntakeRuleSet).where(IntakeRuleSet.version == 1))
        ).scalar_one()
        return copy.deepcopy(row.payload)


@pytest_asyncio.fixture
async def intake(settings: Any) -> AsyncIterator[None]:
    from app.common.feature_flags import FlagKey
    from tests.conftest import _global_flag_on

    async for _ in _global_flag_on(settings, str(FlagKey.INTAKE_DEVICE_ENABLED)):
        yield


@pytest.fixture
def admin(admin_client: Any) -> Any:  # noqa: F811 - the imported fixture
    """A bootstrap SUPERADMIN token is configured; returns the running settings."""
    return admin_client


@pytest_asyncio.fixture
async def restore_rules() -> AsyncIterator[None]:
    """The rule set is global: put version 1 back for every other test."""
    yield
    async with system_session("test: restore intake rules") as db:
        await db.execute(sa.delete(IntakeRuleSet).where(IntakeRuleSet.version > 1))
        await db.execute(
            sa.update(IntakeRuleSet).where(IntakeRuleSet.version == 1).values(active=True)
        )


async def _linked(
    client: AsyncClient, phone: str, *, plan: str | None = None
) -> tuple[dict[str, Any], str]:
    shop = await signed_in_shop(client, phone, plan=plan)
    linked = await client.post(LINK, json=_device(), headers=auth_header(shop))
    assert linked.status_code == 200, linked.text
    return shop, linked.json()["ingest_key"]


# --------------------------------------------------------------------------- #
# Seed and schema
# --------------------------------------------------------------------------- #


async def test_version_1_is_seeded_active_and_canonical() -> None:
    async with system_session("test: active rules") as db:
        active = await rules.active_rule_set(db)
    assert active.version == 1 and active.schema_version == 1
    assert IntakeRulesV1.model_validate(active.payload).model_dump(mode="json") == active.payload
    payload = active.payload
    assert {p["package"]: p["default_enabled"] for p in payload["packages"]} == {
        BUSINESS_SUITE: True,
        WA_BUSINESS: True,
        WHATSAPP: False,
        MESSENGER: False,
    }
    assert payload["buffer_window_minutes"] == 25
    assert payload["address"]["district_list"] == "bd_districts_64"
    assert set(payload["extension"]["hosts"]) == {
        "business.facebook.com",
        "www.messenger.com",
        "web.whatsapp.com",
    }
    # No selector ships before someone has verified it against the live page.
    for host in payload["extension"]["hosts"].values():
        assert host["verified_at"] is None
        assert set(host["selectors"].values()) == {None}
    # The regex source stays ASCII (escapes, no invisible characters).
    assert all(pattern.isascii() for pattern in payload["phone"]["patterns"])


def _device_phones(payload: dict[str, Any], text: str) -> list[str]:
    """What a device does with the phone rules: match, normalise, validate."""
    phone = payload["phone"]
    national = re.compile(phone["national_pattern"])
    found: list[str] = []
    for pattern in phone["patterns"]:
        for match in re.finditer(pattern, text):
            digits = re.sub(r"[^0-9]", "", normalize_digits(match.group(0)))
            if len(digits) == 13 and digits.startswith(phone["country_prefix"]):
                digits = digits[len(phone["country_prefix"]) :]
            if national.search(digits) and digits not in found:
                found.append(digits)
    return found


@pytest.mark.parametrize(
    "text",
    [
        "01700000011",
        "নাম: রহিম, ফোন: ০১৭০০০০০০১২, ঠিকানা: মিরপুর ১০",
        "০১৭০০০০০০১3 mixed digits",
        "+8801700000014",
        "+88 01700-000015 Uttara",
        "+880 1700-000016",
        "Call: 017 0000 0017",
        "8801700000018, 01700000019 duita number",
        "(+88)01700000020",
        "price 1500 tk, qty 2",
        "01200000021 is not allocated",
        "0170000002 is too short",
    ],
)
async def test_seed_phone_rules_find_what_the_server_parser_finds(text: str) -> None:
    payload = await _seed()
    assert _device_phones(payload, text) == [p.national for p in extract_phones(text)]


async def test_seed_phone_rules_ignore_numbers_inside_longer_digit_runs() -> None:
    payload = await _seed()
    assert _device_phones(payload, "invoice 1234501700000022") == []
    assert _device_phones(payload, "017000000223") == []


def _mutated(seed: dict[str, Any], change: Any) -> dict[str, Any]:
    payload = copy.deepcopy(seed)
    change(payload)
    return payload


def _set(path: str, value: Any) -> Any:
    def change(payload: dict[str, Any]) -> None:
        *parents, leaf = path.split(".")
        node: Any = payload
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        node[leaf] = value

    return change


def _host(host: str, value: Any) -> Any:
    def change(payload: dict[str, Any]) -> None:
        payload["extension"]["hosts"][host] = value

    return change


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_set("phone.patterns", [r"01[3-9]\d{8}"]), "not portable"),
        (_set("phone.patterns", [r"(?i)01[3-9][0-9]{8}"]), "not portable"),
        (_set("phone.patterns", [r"(?P<n>01[3-9][0-9]{8})"]), "not portable"),
        (_set("phone.patterns", [r"[0-9]*"]), "empty text"),
        (_set("phone.patterns", [r"01[3-9"]), "does not compile"),
        (_set("phone.required", False), "phone"),
        (_set("score.threshold", 40), "phone number alone"),
        (_set("score.threshold", 500), "higher than any message"),
        (_set("buffer_window_minutes", 0), "buffer_window_minutes"),
        (_set("intent.en", ["অর্ডার"]), "not a latin keyword"),
        (_set("address.bn", ["road"]), "not a Bangla keyword"),
        (_set("address.district_list", "somewhere_else"), "district_list"),
        (_set("packages.1.package", BUSINESS_SUITE), "listed twice"),
        (_set("packages.0.package", "Not A Package"), "package"),
        (_host("example.com", {"verified_at": None}), "hosts"),
        (_host("web.whatsapp.com", {"selectors": {"message": "div.msg"}}), "verified_at"),
        (
            _host(
                "web.whatsapp.com", {"selectors": {"message": "<b>"}, "verified_at": "2026-11-18"}
            ),
            "single CSS",
        ),
        (_set("surprise", True), "surprise"),
    ],
)
async def test_the_schema_refuses_unsafe_or_unportable_rules(change: Any, reason: str) -> None:
    seed = await _seed()
    with pytest.raises(ValidationError) as error:
        IntakeRulesV1.model_validate(_mutated(seed, change))
    assert reason in str(error.value)


async def test_verified_selectors_and_keyword_normalisation_are_accepted() -> None:
    seed = await _seed()
    payload = copy.deepcopy(seed)
    payload["extension"]["hosts"]["web.whatsapp.com"] = {
        "selectors": {"message": "div[role='row'] > div", "message_text": "span.copyable-text"},
        "verified_at": "2026-11-18",
    }
    payload["intent"]["en"] = ["Order", "order", "  Cash   On Delivery "]
    decomposed = "বা" + chr(0x09A1) + chr(0x09BC) + "ি"
    precomposed = "বা" + chr(0x09DC) + "ি"
    payload["address"]["bn"] = [decomposed, precomposed]
    rules_v1 = IntakeRulesV1.model_validate(payload)
    assert rules_v1.intent.en == ["order", "cash on delivery"]
    assert len(rules_v1.address.bn) == 1, "NFC folds both spellings into one keyword"


# --------------------------------------------------------------------------- #
# Device fetch
# --------------------------------------------------------------------------- #


async def test_rules_need_an_ingest_key(
    client: AsyncClient, unique_phone: str, intake: None
) -> None:
    assert (await client.get(RULES)).status_code == 401
    shop = await signed_in_shop(client, unique_phone)
    assert (await client.get(RULES, headers=auth_header(shop))).status_code == 401


async def test_rules_carry_an_etag_and_answer_304_when_unchanged(
    client: AsyncClient, unique_phone: str, intake: None
) -> None:
    _, key = await _linked(client, unique_phone)
    first = await client.get(RULES, headers=_key_header(key))
    assert first.status_code == 200, first.text
    etag = first.headers["etag"]
    assert re.fullmatch(r'"[0-9a-f]{32}"', etag)
    assert "no-cache" in first.headers["cache-control"]
    body = first.json()
    assert body["version"] == 1 and body["rules"] == await _seed()
    assert body["enabled_packages"] == [BUSINESS_SUITE, WA_BUSINESS]
    assert body["covered_packages"] == []
    assert body["refresh_after_seconds"] == 24 * 3600
    assert set(body["district_lists"]["bd_districts_64"]) == set(DISTRICTS)
    assert len(body["district_lists"]["bd_districts_64"]) == 64

    for header in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        again = await client.get(RULES, headers={**_key_header(key), "If-None-Match": header})
        assert again.status_code == 304, header
        assert again.headers["etag"] == etag and again.content == b""
    stale = await client.get(RULES, headers={**_key_header(key), "If-None-Match": '"stale"'})
    assert stale.status_code == 200 and stale.headers["etag"] == etag


async def test_seller_toggles_and_server_coverage_shape_the_device_answer(
    client: AsyncClient, unique_phone: str, intake: None
) -> None:
    shop, key = await _linked(client, unique_phone)
    before = (await client.get(RULES, headers=_key_header(key))).headers["etag"]

    patched = await client.patch(
        SETTINGS,
        json={"package_toggles": {WHATSAPP: True, WA_BUSINESS: False}},
        headers=auth_header(shop),
    )
    assert patched.status_code == 200, patched.text
    states = {p["package"]: p for p in patched.json()["packages"]}
    assert states[WHATSAPP]["toggle"] is True and states[WHATSAPP]["enabled"] is True
    assert states[WA_BUSINESS]["enabled"] is False
    assert states[MESSENGER]["toggle"] is None and states[MESSENGER]["enabled"] is False

    changed = await client.get(RULES, headers={**_key_header(key), "If-None-Match": before})
    assert changed.status_code == 200 and changed.headers["etag"] != before
    assert changed.json()["enabled_packages"] == [BUSINESS_SUITE, WHATSAPP]

    # Phase 6: an official connection covers Business Suite; devices skip it.
    async with system_session("test: cover a package") as db:
        await rules.set_covered_packages(db, uuid.UUID(shop["tenant_id"]), [BUSINESS_SUITE])
    covered = (await client.get(RULES, headers=_key_header(key))).json()
    assert covered["enabled_packages"] == [WHATSAPP]
    assert covered["covered_packages"] == [BUSINESS_SUITE]
    view = {
        p["package"]: p
        for p in (await client.get(SETTINGS, headers=auth_header(shop))).json()["packages"]
    }
    assert view[BUSINESS_SUITE]["covered"] is True and view[BUSINESS_SUITE]["enabled"] is False

    reset = await client.patch(
        SETTINGS, json={"package_toggles": {WHATSAPP: None}}, headers=auth_header(shop)
    )
    assert {p["package"]: p["toggle"] for p in reset.json()["packages"]}[WHATSAPP] is None
    assert (await client.get(RULES, headers=_key_header(key))).json()["enabled_packages"] == []

    audits = await _audit_count(AuditAction.INTAKE_SETTINGS_UPDATED, tenant_id=shop["tenant_id"])
    assert audits == 2


async def test_settings_refuse_unknown_apps_and_server_only_fields(
    client: AsyncClient, unique_phone: str, intake: None
) -> None:
    shop = await signed_in_shop(client, unique_phone)
    unknown = await client.patch(
        SETTINGS, json={"package_toggles": {"com.example.chat": True}}, headers=auth_header(shop)
    )
    assert unknown.status_code == 422 and unknown.json()["code"] == "VALIDATION_ERROR"
    sneaky = await client.patch(
        SETTINGS,
        json={"package_toggles": {WHATSAPP: True}, "covered_packages": []},
        headers=auth_header(shop),
    )
    assert sneaky.status_code == 422


async def test_settings_need_the_flag_settings_manage_and_a_session(
    client: AsyncClient, unique_phone: str, intake: None
) -> None:
    shop, key = await _linked(client, unique_phone, plan="pro")
    assert (await client.get(SETTINGS, headers=auth_header(shop))).status_code == 200
    assert (await client.get(SETTINGS, headers=_key_header(key))).status_code == 401
    member = await _member_session(client, shop, _unique_phone(), TenantRole.ORDER_OPERATOR)
    assert (await client.get(SETTINGS, headers=auth_header(member))).status_code == 403


async def test_settings_are_off_without_the_flag(client: AsyncClient, unique_phone: str) -> None:
    shop = await signed_in_shop(client, unique_phone)
    off = await client.get(SETTINGS, headers=auth_header(shop))
    assert off.status_code == 403 and off.json()["code"] == "FEATURE_DISABLED"


# --------------------------------------------------------------------------- #
# Admin versions
# --------------------------------------------------------------------------- #


async def _audit_count(action: AuditAction, **where: Any) -> int:
    async with system_session("test: audit") as db:
        stmt = (
            sa.select(sa.func.count()).select_from(AuditLog).where(AuditLog.action == str(action))
        )
        if "tenant_id" in where:
            stmt = stmt.where(AuditLog.tenant_id == uuid.UUID(where["tenant_id"]))
        return int((await db.execute(stmt)).scalar_one())


async def test_a_new_version_is_inactive_until_activated_and_rolls_back(
    client: AsyncClient,
    unique_phone: str,
    intake: None,
    admin: Any,
    restore_rules: None,
) -> None:
    _, key = await _linked(client, unique_phone)
    etag_v1 = (await client.get(RULES, headers=_key_header(key))).headers["etag"]
    created_before = await _audit_count(AuditAction.ADMIN_INTAKE_RULES_CREATED)
    activated_before = await _audit_count(AuditAction.ADMIN_INTAKE_RULES_ACTIVATED)

    payload = await _seed()
    payload["score"]["threshold"] = 70
    payload["intent"]["en"] = [*payload["intent"]["en"], "  Pre-Order "]
    created = await client.post(
        ADMIN, json={"payload": payload, "note": "tighter threshold"}, headers=admin_header()
    )
    assert created.status_code == 201, created.text
    v2 = created.json()
    assert v2["version"] == 2 and v2["active"] is False and v2["note"] == "tighter threshold"
    assert v2["payload"]["intent"]["en"][-1] == "pre-order", "stored normalised"

    unchanged = await client.get(RULES, headers={**_key_header(key), "If-None-Match": etag_v1})
    assert unchanged.status_code == 304, "an inactive version changes nothing"

    no_reason = await client.post(
        f"{ADMIN}/2/activate", json={"reason": "go"}, headers=admin_header()
    )
    assert no_reason.status_code == 422
    activated = await client.post(
        f"{ADMIN}/2/activate",
        json={"reason": "pilot showed too many alerts"},
        headers=admin_header(),
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["active"] is True and activated.json()["activated_by"]

    now = await client.get(RULES, headers={**_key_header(key), "If-None-Match": etag_v1})
    assert now.status_code == 200 and now.json()["version"] == 2
    assert now.json()["rules"]["score"]["threshold"] == 70

    rollback = await client.post(
        f"{ADMIN}/1/activate", json={"reason": "roll back to the seed"}, headers=admin_header()
    )
    assert rollback.status_code == 200
    again = await client.post(
        f"{ADMIN}/1/activate", json={"reason": "double click on rollback"}, headers=admin_header()
    )
    assert again.status_code == 200
    listing = (await client.get(ADMIN, headers=admin_header())).json()
    assert [(row["version"], row["active"]) for row in listing] == [(2, False), (1, True)]
    back = await client.get(RULES, headers={**_key_header(key), "If-None-Match": etag_v1})
    assert back.status_code == 304, "rollback restores the exact v1 answer"

    assert await _audit_count(AuditAction.ADMIN_INTAKE_RULES_CREATED) == created_before + 1
    # v2 on, back to v1; the repeated activation of v1 changed nothing, so no row.
    assert await _audit_count(AuditAction.ADMIN_INTAKE_RULES_ACTIVATED) == activated_before + 2


async def test_admin_rules_need_ops_and_a_valid_payload(
    client: AsyncClient, admin: Any, restore_rules: None
) -> None:
    payload = await _seed()
    assert (await client.post(ADMIN, json={"payload": payload})).status_code == 401

    support_token = f"support-{uuid.uuid4().hex}-intake-rules-token-0123456789"
    await _provision_admin(PlatformAdminRole.SUPPORT, support_token, admin)
    assert (await client.get(ADMIN, headers=admin_header(support_token))).status_code == 200
    refused = await client.post(
        ADMIN, json={"payload": payload}, headers=admin_header(support_token)
    )
    assert refused.status_code == 403
    assert (
        await client.post(
            f"{ADMIN}/1/activate",
            json={"reason": "support should not"},
            headers=admin_header(support_token),
        )
    ).status_code == 403

    bad = copy.deepcopy(payload)
    bad["score"]["threshold"] = 10
    assert (
        await client.post(ADMIN, json={"payload": bad}, headers=admin_header())
    ).status_code == 422
    missing = await client.post(
        f"{ADMIN}/999/activate", json={"reason": "no such version"}, headers=admin_header()
    )
    assert missing.status_code == 404
