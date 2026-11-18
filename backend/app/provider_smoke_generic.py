"""Operator smoke checks for Pathao and RedX (V4 WP0.7).

Same three levels as the Steadfast smoke in :mod:`app.provider_smoke`:

1. **read-only (default)** — validate the credentials, list the merchant's
   stores, and look up one parcel when the operator names it;
2. ``--probe-undocumented`` — read the location/catalogue lists and the token
   answer, recording only *field names and counts*, which is how the
   manifests' ``unknowns`` (list envelope depth, token lifetime, pagination)
   become facts;
3. ``--allow-create`` — one real parcel through the adapter's own
   ``create_consignment`` (the same path the app books with), only with a
   recipient fixture and the typed confirmation done in ``main()``.

Every call is an existing client/adapter method; nothing here builds a
request by hand, so no endpoint or field is invented.
"""

from __future__ import annotations

import argparse
import os
import uuid
from typing import Any

import sqlalchemy as sa

from app.common.money import Money
from app.common.phone import try_normalize_bd_phone
from app.core.config import get_settings
from app.core.security import CredentialVault
from app.couriers.adapter import BookingOutcome, BookingRequest, Unavailable
from app.couriers.capabilities import Capability, load_manifest
from app.couriers.credentials import build_credentials
from app.couriers.http import ProviderError
from app.couriers.models import CourierAccount
from app.couriers.registry import build_registry

__all__ = ["ENV_VARIABLES", "GENERIC_PROVIDERS", "run_generic_smoke"]

GENERIC_PROVIDERS = ("pathao", "redx")

#: Per provider: (primary variable, secondary variable or None).
ENV_VARIABLES: dict[str, tuple[str, str | None]] = {
    "pathao": ("PATHAO_SMOKE_CLIENT_ID", "PATHAO_SMOKE_CLIENT_SECRET"),
    "redx": ("REDX_SMOKE_API_TOKEN", None),
}


def _error(exc: ProviderError) -> dict[str, Any]:
    return exc.as_log_context()


def _fields(rows: Any) -> dict[str, Any]:
    """Count and field names only — never payload values."""
    if isinstance(rows, list):
        return {
            "count": len(rows),
            "fields": sorted({key for row in rows for key in (getattr(row, "raw", None) or {})}),
        }
    return {"fields": sorted(getattr(rows, "raw", None) or {})}


def _credentials_from_env(provider: str, sandbox: bool) -> tuple[Any, str]:
    primary_name, secondary_name = ENV_VARIABLES[provider]
    primary = os.environ.get(primary_name, "").strip()
    secondary = os.environ.get(secondary_name, "").strip() if secondary_name else ""
    if not primary or (secondary_name and not secondary):
        needed = " and ".join(n for n in (primary_name, secondary_name) if n)
        raise SystemExit(f"--env-credentials needs {needed} to be set.")
    return (
        build_credentials(
            provider, primary=primary, secondary=secondary, config={"sandbox": sandbox}
        ),
        "environment",
    )


async def _credentials_from_account(
    provider: str, account_id: uuid.UUID | None, tenant_id: uuid.UUID | None
) -> tuple[Any, str]:
    from app.db.session import system_session
    from app.db.tenancy import allow_cross_tenant

    vault = CredentialVault(get_settings())
    async with system_session("operator: provider smoke test") as session:
        stmt = sa.select(CourierAccount).where(CourierAccount.provider == provider)
        if account_id is not None:
            stmt = stmt.where(CourierAccount.id == account_id)
        if tenant_id is not None:
            stmt = stmt.where(CourierAccount.tenant_id == tenant_id)
        with allow_cross_tenant("operator smoke test: locate courier account"):
            account = (await session.execute(stmt)).scalars().first()
        if account is None:
            primary_name, secondary_name = ENV_VARIABLES[provider]
            names = " and ".join(n for n in (primary_name, secondary_name) if n)
            raise SystemExit(
                f"No {provider} account found. Pass --account-id, --tenant-id, or use "
                f"--env-credentials with {names}."
            )
        if not account.has_credentials:
            raise SystemExit(
                f"Courier account {account.id} has no stored credentials (status {account.status})."
            )
        context = account.vault_context
        credentials = build_credentials(
            provider,
            primary=vault.decrypt(account.api_key_encrypted or "", context=context),
            secondary=vault.decrypt(account.secret_key_encrypted or "", context=context),
            config=dict(account.metadata_json or {}),
        )
        return credentials, str(account.id)


async def run_generic_smoke(args: argparse.Namespace, report: Any, result_type: Any) -> Any:
    """Run the Pathao/RedX checks into ``report`` (an ``app.provider_smoke.SmokeReport``)."""
    provider = args.provider
    add = report.add
    manifest = load_manifest(provider)
    add(
        result_type(
            name="manifest",
            ok=manifest is not None,
            detail=(
                f"documentation {manifest.documentation_version}, verified {manifest.verified_at}"
                if manifest
                else "no provider manifest found"
            ),
            observed={
                "capabilities": {str(c): str(manifest.state(c)) for c in Capability}
                if manifest
                else {},
                "unknowns": manifest.unknowns if manifest else {},
            },
        )
    )

    if args.env_credentials:
        credentials, source = _credentials_from_env(provider, bool(args.sandbox))
    else:
        credentials, source = await _credentials_from_account(
            provider,
            uuid.UUID(args.account_id) if args.account_id else None,
            uuid.UUID(args.tenant_id) if args.tenant_id else None,
        )
    add(
        result_type(
            name="credentials",
            ok=True,
            detail=f"loaded from {source}",
            observed={"sandbox": bool(getattr(credentials, "sandbox", False))},
        )
    )

    registry = build_registry(get_settings())
    adapter = registry.get(provider)
    if adapter is None:  # pragma: no cover - registry is fixed
        raise SystemExit(f"No {provider} adapter is registered.")
    try:
        # --- authenticate: the adapter's own credential check ---------------
        try:
            validation = await adapter.validate_credentials(credentials)
        except ProviderError as exc:
            add(
                result_type(
                    name="authenticate", ok=False, detail=str(exc.kind), observed=_error(exc)
                )
            )
            return report
        add(
            result_type(
                name="authenticate",
                ok=validation.valid,
                detail=validation.message or ("accepted" if validation.valid else "refused"),
                observed={
                    "rejected": validation.rejected,
                    "detected_capabilities": sorted(
                        str(c) for c in validation.detected_capabilities
                    ),
                },
            )
        )
        if not validation.valid:
            return report

        # --- stores ---------------------------------------------------------
        stores: Any = None
        try:
            stores = await adapter.list_stores(credentials)
            if isinstance(stores, Unavailable):
                add(result_type(name="stores", ok=True, skipped=True, detail=str(stores)))
                stores = None
            else:
                add(
                    result_type(
                        name="stores",
                        ok=True,
                        detail=f"{len(stores)} store(s)",
                        observed={"count": len(stores)},
                    )
                )
        except ProviderError as exc:
            add(result_type(name="stores", ok=False, detail=str(exc.kind), observed=_error(exc)))

        # --- one parcel, only when the operator names it ---------------------
        reference = args.tracking_code or args.consignment_id
        if reference:
            try:
                status = await adapter.get_status(credentials, reference)
                if isinstance(status, Unavailable):
                    add(result_type(name="status", ok=True, skipped=True, detail=str(status)))
                else:
                    add(
                        result_type(
                            name="status",
                            ok=True,
                            detail=f"status = {status.normalized_status!r}",
                            observed={"provider_raw_status": status.raw_status},
                        )
                    )
            except ProviderError as exc:
                add(
                    result_type(name="status", ok=False, detail=str(exc.kind), observed=_error(exc))
                )

        if args.probe_undocumented:
            await _probe(provider, adapter, credentials, stores, report, result_type)
        else:
            add(
                result_type(
                    name="undocumented shapes",
                    ok=True,
                    skipped=True,
                    detail="pass --probe-undocumented to record list and token shapes",
                )
            )

        if args.allow_create:
            await _create(provider, adapter, credentials, stores, args, report, result_type)
        else:
            add(
                result_type(
                    name="create parcel",
                    ok=True,
                    skipped=True,
                    detail="not attempted (pass --allow-create to create a real parcel)",
                )
            )
    finally:
        await registry.aclose()
    return report


async def _probe(
    provider: str, adapter: Any, credentials: Any, stores: Any, report: Any, result_type: Any
) -> None:
    client = adapter.client
    calls: list[tuple[str, Any]] = []
    if provider == "pathao":
        calls.append(("token (lifetime)", None))
        calls.append(("cities", client.list_cities(credentials)))
    else:
        calls.append(("areas", client.list_areas(credentials)))
    for name, coroutine in calls:
        try:
            if name == "token (lifetime)":
                token = (await client.login(credentials)).value
                observed = {
                    "fields": sorted(token.raw),
                    "expires_in": token.expires_in,
                    "has_refresh_token": token.refresh_token is not None,
                }
                rows = None
            else:
                rows = (await coroutine).value
                observed = _fields(rows)
            report.add(
                result_type(
                    name=name, ok=True, detail="answered; shape recorded", observed=observed
                )
            )
            if provider == "pathao" and name == "cities" and rows:
                await _probe_pathao_children(client, credentials, rows[0], report, result_type)
        except ProviderError as exc:
            report.add(result_type(name=name, ok=False, detail=str(exc.kind), observed=_error(exc)))


async def _probe_pathao_children(
    client: Any, credentials: Any, city: Any, report: Any, result_type: Any
) -> None:
    try:
        zones = (await client.list_zones(credentials, city.city_id)).value
        report.add(
            result_type(
                name="zones (first city)", ok=True, detail="answered", observed=_fields(zones)
            )
        )
        if zones:
            areas = (await client.list_areas(credentials, zones[0].zone_id)).value
            report.add(
                result_type(
                    name="areas (first zone)", ok=True, detail="answered", observed=_fields(areas)
                )
            )
    except ProviderError as exc:
        report.add(
            result_type(name="zones/areas", ok=False, detail=str(exc.kind), observed=_error(exc))
        )


async def _create(
    provider: str,
    adapter: Any,
    credentials: Any,
    stores: Any,
    args: argparse.Namespace,
    report: Any,
    result_type: Any,
) -> None:
    phone = try_normalize_bd_phone(args.recipient_phone or "")
    if not (args.recipient_name and phone and args.recipient_address):
        report.add(
            result_type(
                name="create parcel",
                ok=False,
                detail=(
                    "--allow-create needs --recipient-name, a valid --recipient-phone and "
                    "--recipient-address. There is deliberately no default recipient."
                ),
            )
        )
        return
    if provider == "redx" and not (args.delivery_area_id and args.delivery_area_name):
        report.add(
            result_type(
                name="create parcel",
                ok=False,
                detail="RedX needs --delivery-area-id and --delivery-area-name (from the areas list).",
            )
        )
        return
    reference = args.create_invoice
    if not reference:
        report.add(
            result_type(
                name="create parcel", ok=True, skipped=True, detail="cancelled at the prompt"
            )
        )
        return
    store_reference = args.store_id or (stores[0].provider_store_id if stores else None)
    request = BookingRequest(
        order_id=uuid.uuid4(),
        merchant_reference=reference,
        recipient_name=args.recipient_name,
        recipient_phone_e164=phone.e164,
        recipient_address=args.recipient_address,
        cod_amount=Money.from_taka(int(args.cod_amount)),
        item_description="Smoke test",
        note="ecomsbd operator smoke test",
        store_reference=store_reference,
        delivery_area_id=args.delivery_area_id,
        delivery_area_name=args.delivery_area_name,
    )
    try:
        result = await adapter.create_consignment(credentials, request, reference)
        report.add(
            result_type(
                name="create parcel",
                ok=result.outcome is BookingOutcome.BOOKED,
                detail=f"outcome {result.outcome}",
                observed={
                    "merchant_reference": reference,
                    "consignment": getattr(result.consignment, "provider_consignment_id", None),
                    "tracking": getattr(result.consignment, "tracking_code", None),
                    # Ambiguous: the parcel may exist under this reference. Check it.
                    "reference_to_check": reference if result.is_ambiguous else None,
                },
            )
        )
    except ProviderError as exc:
        report.add(
            result_type(
                name="create parcel",
                ok=False,
                detail=str(exc.kind),
                observed={**_error(exc), "reference_to_check": reference},
            )
        )
