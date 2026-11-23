"""Intake rules: the active rule set, the shop's overrides, and versions (V4 WP1.2).

Devices fetch ``GET /v1/intake/rules``: the active version, the district list it
references (resolved from the parser's gazetteer, so the list lives in one
place), and which apps this shop's device should read. The body is serialised
once, canonically, and its SHA-256 is the ETag, so an unchanged answer is a 304.

A package is read when it is in the active rules, **not** covered by an official
connection (that path already delivers its messages), and the seller's toggle —
or, without one, the rule's ``default_enabled`` — says so.

Versions are append-only apart from activation. Activating locks every row
and leaves exactly one active, so two operators activating at once cannot
leave two versions live; rollback is activating an older version.
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError, ServiceUnavailableError, ValidationError
from app.intake.models import IntakeRuleSet, ShopIntakeSettings
from app.intake.rules_schema import IntakeRulesV1, canonical_json
from app.orders.parser.districts import DISTRICTS

__all__ = [
    "Activation",
    "DeviceRules",
    "PackageState",
    "activate_version",
    "active_rule_set",
    "create_version",
    "device_rules",
    "district_list",
    "etag_matches",
    "list_versions",
    "package_states",
    "set_covered_packages",
    "shop_settings",
    "update_toggles",
]


def district_list(list_id: str) -> dict[str, list[str]]:
    """Canonical district name → every spelling (NFC), for ``bd_districts_64``."""
    if list_id != "bd_districts_64":
        raise KeyError(list_id)
    return {
        name: [unicodedata.normalize("NFC", spelling) for spelling in spellings]
        for name, spellings in DISTRICTS.items()
    }


async def active_rule_set(db: AsyncSession) -> IntakeRuleSet:
    row = (
        await db.execute(
            sa.select(IntakeRuleSet)
            .where(IntakeRuleSet.active.is_(True))
            .order_by(IntakeRuleSet.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        # Devices keep their cached copy; nothing is guessed.
        raise ServiceUnavailableError("No intake rules are active")
    return row


async def shop_settings(db: AsyncSession, tenant_id: uuid.UUID) -> ShopIntakeSettings | None:
    return (
        await db.execute(
            sa.select(ShopIntakeSettings).where(ShopIntakeSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class PackageState:
    package: str
    label: str
    default_enabled: bool
    #: The seller's choice; ``None`` = use ``default_enabled``.
    toggle: bool | None
    covered: bool
    enabled: bool


def package_states(
    payload: dict[str, Any], settings: ShopIntakeSettings | None
) -> list[PackageState]:
    covered = set(settings.covered_packages) if settings is not None else set()
    toggles = settings.package_toggles if settings is not None else {}
    states = []
    for rule in payload["packages"]:
        name = rule["package"]
        raw = toggles.get(name)
        toggle = raw if isinstance(raw, bool) else None
        is_covered = name in covered
        wanted = rule["default_enabled"] if toggle is None else toggle
        states.append(
            PackageState(
                package=name,
                label=rule["label"],
                default_enabled=rule["default_enabled"],
                toggle=toggle,
                covered=is_covered,
                enabled=wanted and not is_covered,
            )
        )
    return states


@dataclass(frozen=True, slots=True)
class DeviceRules:
    body: bytes
    etag: str


def device_rules(rule_set: IntakeRuleSet, settings: ShopIntakeSettings | None) -> DeviceRules:
    payload = rule_set.payload
    states = package_states(payload, settings)
    list_id = payload["address"]["district_list"]
    body = {
        "version": rule_set.version,
        "schema_version": rule_set.schema_version,
        "rules": payload,
        "district_lists": {list_id: district_list(list_id)},
        "enabled_packages": [state.package for state in states if state.enabled],
        "covered_packages": sorted(state.package for state in states if state.covered),
        "refresh_after_seconds": int(payload["refresh_hours"]) * 3600,
    }
    raw = canonical_json(body).encode()
    return DeviceRules(body=raw, etag='"' + hashlib.sha256(raw).hexdigest()[:32] + '"')


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 weak comparison, as ``If-None-Match`` requires."""
    if not if_none_match:
        return False
    for candidate in if_none_match.split(","):
        tag = candidate.strip()
        if tag == "*":
            return True
        if tag.removeprefix("W/") == etag:
            return True
    return False


async def _settings_row(db: AsyncSession, tenant_id: uuid.UUID) -> ShopIntakeSettings:
    row = await shop_settings(db, tenant_id)
    if row is None:
        row = ShopIntakeSettings(tenant_id=tenant_id, covered_packages=[], package_toggles={})
        db.add(row)
    return row


async def update_toggles(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    changes: dict[str, bool | None],
    *,
    known_packages: Iterable[str],
) -> ShopIntakeSettings:
    """Apply the seller's per-app choices; ``None`` returns an app to its default.

    Toggles for apps the active rules no longer list are dropped on the way.
    """
    known = set(known_packages)
    unknown = sorted(set(changes) - known)
    if unknown:
        raise ValidationError(
            "Unknown app", details={"field": "package_toggles", "packages": unknown}
        )
    row = await _settings_row(db, tenant_id)
    toggles = {
        name: value
        for name, value in row.package_toggles.items()
        if name in known and isinstance(value, bool)
    }
    for name, value in changes.items():
        if value is None:
            toggles.pop(name, None)
        else:
            toggles[name] = value
    row.package_toggles = toggles  # a new dict, so the JSON change is tracked
    await _flush_settings(db)
    return row


async def set_covered_packages(
    db: AsyncSession, tenant_id: uuid.UUID, packages: Iterable[str]
) -> ShopIntakeSettings:
    """Server-side only: the apps an official connection covers (Phase 6 calls
    this when a Messenger/WhatsApp connection becomes active or disconnects)."""
    row = await _settings_row(db, tenant_id)
    row.covered_packages = sorted(set(packages))
    await _flush_settings(db)
    return row


async def _flush_settings(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError:
        # Two first writes for one shop at once; the other one won.
        raise ConflictError("Settings changed at the same time; try again") from None


async def list_versions(db: AsyncSession, *, limit: int = 20) -> list[IntakeRuleSet]:
    stmt = sa.select(IntakeRuleSet).order_by(IntakeRuleSet.version.desc()).limit(limit)
    return list((await db.scalars(stmt)).all())


async def create_version(
    db: AsyncSession, rules: IntakeRulesV1, *, created_by: str, note: str | None
) -> IntakeRuleSet:
    """Publish a new version. It is inactive until :func:`activate_version`."""
    latest = (await db.execute(sa.select(sa.func.max(IntakeRuleSet.version)))).scalar()
    row = IntakeRuleSet(
        version=int(latest or 0) + 1,
        schema_version=rules.schema_version,
        payload=rules.model_dump(mode="json"),
        active=False,
        created_by=created_by,
        note=note,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise ConflictError("Another version was published at the same time; try again") from None
    return row


@dataclass(frozen=True, slots=True)
class Activation:
    rule_set: IntakeRuleSet
    previous_version: int | None
    changed: bool


async def activate_version(db: AsyncSession, version: int, *, activated_by: str) -> Activation:
    rows = list(
        (
            await db.scalars(
                sa.select(IntakeRuleSet).order_by(IntakeRuleSet.version).with_for_update()
            )
        ).all()
    )
    target = next((row for row in rows if row.version == version), None)
    if target is None:
        raise NotFoundError("No such intake rules version", details={"version": version})
    # Stored payloads were valid when published; re-check against today's schema.
    try:
        IntakeRulesV1.model_validate(target.payload)
    except PydanticValidationError:
        raise ValidationError(
            "This version no longer passes the rules schema", details={"version": version}
        ) from None
    others = [row for row in rows if row.active and row is not target]
    previous = max((row.version for row in others), default=None)
    if target.active and not others:
        return Activation(rule_set=target, previous_version=None, changed=False)
    for row in others:
        row.active = False
    if not target.active:
        target.active = True
        target.activated_at = utc_now()
        target.activated_by = activated_by
    await db.flush()
    return Activation(rule_set=target, previous_version=previous, changed=True)
