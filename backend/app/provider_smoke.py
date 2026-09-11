"""Operator smoke test for a courier provider.

Brief sections 42 and 43::

    python -m app.provider_smoke steadfast --account-id <uuid>
    python -m app.provider_smoke steadfast --tenant-id <uuid> --invoice CP-20260911-0042
    python -m app.provider_smoke steadfast --env-credentials --json

This is the one place in the codebase that talks to a *live* provider on
purpose. Everything about it is arranged so that running it cannot surprise
anyone:

*   **Credentials are never passed on the command line.** They come from the
    encrypted courier account, or from ``STEADFAST_SMOKE_API_KEY`` /
    ``STEADFAST_SMOKE_SECRET_KEY``. A key in ``argv`` is a key in the shell
    history, in ``ps`` output and in the CI log.

*   **Every default check is a read.** Authentication, balance, and — when the
    operator names one — a status lookup. None of them creates anything, costs
    anything or changes a parcel.

*   **Creating a parcel takes two deliberate acts.** ``--allow-create`` *and* a
    typed confirmation, or ``--yes`` for an unattended run an operator has
    already decided on. It also refuses outright unless a recipient fixture is
    given, so there is no default address a stray invocation could ship to.

*   **It refuses to run in CI by default.** ``CI=true`` in the environment
    blocks the whole command unless ``--allow-ci`` is passed. Brief section 43:
    GitHub Actions must never depend on Steadfast being up, and the way that
    rule breaks is someone adding this to a workflow "just to check".

The output doubles as evidence: every check reports what it observed, including
the field names a path-only endpoint actually returned, which is what turns
``UNVERIFIED`` in the provider notes into a documented fact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.context import ActorType, RequestContext, set_context
from app.core.logging import configure_logging
from app.core.security import CredentialVault
from app.couriers.capabilities import Capability, load_manifest
from app.couriers.models import CourierAccount
from app.couriers.registry import build_registry
from app.couriers.steadfast.adapter import SteadfastAdapter
from app.couriers.steadfast.client import StatusLookupKind, SteadfastCredentials
from app.couriers.steadfast.errors import SteadfastError

__all__ = ["SmokeReport", "SmokeResult", "main", "run_smoke"]

#: Environment variables the operator may use instead of a stored account.
ENV_API_KEY = "STEADFAST_SMOKE_API_KEY"
ENV_SECRET_KEY = "STEADFAST_SMOKE_SECRET_KEY"  # noqa: S105 - a variable *name*


@dataclass(slots=True)
class SmokeResult:
    """One check, and what it found."""

    name: str
    ok: bool
    detail: str
    observed: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False

    def render(self) -> str:
        mark = "SKIP" if self.skipped else ("PASS" if self.ok else "FAIL")
        line = f"[{mark}] {self.name}: {self.detail}"
        if self.observed:
            line += f"\n       observed: {json.dumps(self.observed, default=str)}"
        return line

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "skipped": self.skipped,
            "detail": self.detail,
            "observed": self.observed,
        }


@dataclass(slots=True)
class SmokeReport:
    provider: str
    started_at: str
    results: list[SmokeResult] = field(default_factory=list)

    def add(self, result: SmokeResult) -> SmokeResult:
        self.results.append(result)
        return result

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok and not r.skipped)

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    def render(self) -> str:
        header = f"Steadfast smoke test — {self.provider} — {self.started_at}"
        lines = [header, "=" * len(header)]
        lines.extend(result.render() for result in self.results)
        lines.append("")
        lines.append(
            f"{sum(1 for r in self.results if r.ok and not r.skipped)} passed, "
            f"{self.failed} failed, "
            f"{sum(1 for r in self.results if r.skipped)} skipped"
        )
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "started_at": self.started_at,
            "failed": self.failed,
            "results": [result.as_dict() for result in self.results],
        }


async def _credentials_from_account(
    account_id: uuid.UUID | None, tenant_id: uuid.UUID | None
) -> tuple[SteadfastCredentials, str]:
    """Read stored credentials out of the vault.

    Runs on a system session with the tenant installed, exactly as a background
    job does, so the same tenancy guard applies to a tool an operator runs by
    hand.
    """
    from app.db.session import system_session
    from app.db.tenancy import allow_cross_tenant

    settings = get_settings()
    vault = CredentialVault(settings)

    async with system_session("operator: provider smoke test") as session:
        stmt = sa.select(CourierAccount).where(CourierAccount.provider == "steadfast")
        if account_id is not None:
            stmt = stmt.where(CourierAccount.id == account_id)
        if tenant_id is not None:
            stmt = stmt.where(CourierAccount.tenant_id == tenant_id)

        with allow_cross_tenant("operator smoke test: locate courier account"):
            account = (await session.execute(stmt)).scalars().first()

        if account is None:
            raise SystemExit(
                "No Steadfast account found. Pass --account-id, --tenant-id, or use "
                f"--env-credentials with {ENV_API_KEY} and {ENV_SECRET_KEY}."
            )
        if not account.has_credentials:
            raise SystemExit(
                f"Courier account {account.id} has no stored credentials (status {account.status})."
            )

        credentials = SteadfastCredentials(
            api_key=vault.decrypt(account.api_key_encrypted or "", context=account.vault_context),
            secret_key=vault.decrypt(
                account.secret_key_encrypted or "", context=account.vault_context
            ),
        )
        return credentials, str(account.id)


def _credentials_from_env() -> tuple[SteadfastCredentials, str]:
    api_key = os.environ.get(ENV_API_KEY, "").strip()
    secret_key = os.environ.get(ENV_SECRET_KEY, "").strip()
    if not api_key or not secret_key:
        raise SystemExit(
            f"--env-credentials needs both {ENV_API_KEY} and {ENV_SECRET_KEY} to be set."
        )
    return SteadfastCredentials(api_key=api_key, secret_key=secret_key), "environment"


async def run_smoke(args: argparse.Namespace) -> SmokeReport:
    """Run the requested checks against a live provider."""
    report = SmokeReport(provider="steadfast", started_at=utc_now().isoformat())

    manifest = load_manifest("steadfast")
    report.add(
        SmokeResult(
            name="manifest",
            ok=manifest is not None,
            detail=(
                f"documentation {manifest.documentation_version}, verified {manifest.verified_at}"
                if manifest
                else "no provider manifest found"
            ),
            observed={
                "capabilities": {
                    str(capability): str(manifest.state(capability)) for capability in Capability
                }
                if manifest
                else {},
                "unknowns": manifest.unknowns if manifest else {},
            },
        )
    )

    if args.env_credentials:
        credentials, source = _credentials_from_env()
    else:
        credentials, source = await _credentials_from_account(
            uuid.UUID(args.account_id) if args.account_id else None,
            uuid.UUID(args.tenant_id) if args.tenant_id else None,
        )
    report.add(
        SmokeResult(
            name="credentials",
            ok=True,
            detail=f"loaded from {source}",
            # The masked hint, never the key.
            observed={"identifier": credentials.masked_identifier},
        )
    )

    registry = build_registry(get_settings())
    adapter = registry.get("steadfast")
    if not isinstance(adapter, SteadfastAdapter):  # pragma: no cover - registry is fixed
        raise SystemExit("No Steadfast adapter is registered.")

    try:
        # --- authentication + balance (one call; the safest documented read) --
        try:
            balance = await adapter.get_balance(credentials)
            report.add(
                SmokeResult(
                    name="authenticate + balance",
                    ok=True,
                    detail="GET /get_balance answered",
                    observed={"current_balance_paisa": getattr(balance, "paisa", None)},
                )
            )
        except SteadfastError as exc:
            report.add(
                SmokeResult(
                    name="authenticate + balance",
                    ok=False,
                    detail=f"{exc.kind}: {exc}",
                    observed=exc.as_log_context(),
                )
            )
            # Nothing further is meaningful without working credentials.
            return report

        # --- status lookup, only for a reference the operator supplied --------
        for kind, value in (
            (StatusLookupKind.INVOICE, args.invoice),
            (StatusLookupKind.CONSIGNMENT_ID, args.consignment_id),
            (StatusLookupKind.TRACKING_CODE, args.tracking_code),
        ):
            if not value:
                continue
            try:
                call = await adapter.client.get_status(credentials, value, kind=kind)
                report.add(
                    SmokeResult(
                        name=f"status by {kind}",
                        ok=True,
                        detail=f"delivery_status = {call.value.delivery_status!r}",
                        observed={
                            "documented_status": call.value.is_documented,
                            "raw": call.value.raw,
                        },
                    )
                )
            except SteadfastError as exc:
                report.add(
                    SmokeResult(
                        name=f"status by {kind}",
                        ok=False,
                        detail=f"{exc.kind}: {exc}",
                        observed=exc.as_log_context(),
                    )
                )

        # --- the path-only endpoints: what do they actually return? -----------
        if args.probe_undocumented:
            await _probe_undocumented(adapter, credentials, report)
        else:
            report.add(
                SmokeResult(
                    name="undocumented endpoints",
                    ok=True,
                    skipped=True,
                    detail="pass --probe-undocumented to record their real shapes",
                )
            )

        # --- create, only with two deliberate acts ---------------------------
        if args.allow_create:
            await _create_probe(adapter, credentials, args, report)
        else:
            report.add(
                SmokeResult(
                    name="create parcel",
                    ok=True,
                    skipped=True,
                    detail="not attempted (pass --allow-create to create a real parcel)",
                )
            )
    finally:
        await registry.aclose()

    return report


async def _probe_undocumented(
    adapter: SteadfastAdapter, credentials: SteadfastCredentials, report: SmokeReport
) -> None:
    """Call the endpoints with no documented schema and record what came back.

    This is how ``UNVERIFIED`` becomes a fact. The field names in the output go
    straight into ``docs/providers/steadfast/IMPLEMENTATION.md``, replacing an
    inference with an observation.
    """
    probes: list[tuple[str, Any]] = [
        ("GET /payments", adapter.client.list_payments(credentials)),
        ("GET /get_return_requests", adapter.client.list_return_requests(credentials)),
        ("GET /police_stations", adapter.client.list_police_stations(credentials)),
    ]
    for name, coroutine in probes:
        try:
            call = await coroutine
            observed = _observed_fields(call.value)
            report.add(
                SmokeResult(
                    name=name,
                    ok=True,
                    detail="answered; field names recorded below",
                    observed=observed,
                )
            )
        except SteadfastError as exc:
            report.add(
                SmokeResult(
                    name=name, ok=False, detail=f"{exc.kind}: {exc}", observed=exc.as_log_context()
                )
            )


def _observed_fields(value: Any) -> dict[str, Any]:
    """Field names the provider actually used, without dumping payload content.

    Names only: a real payments response contains a merchant's settlement
    amounts, and a smoke-test transcript gets pasted into tickets.
    """
    if isinstance(value, list):
        return {
            "count": len(value),
            "fields": sorted({key for row in value for key in getattr(row, "raw", {})}),
        }
    payments = getattr(value, "payments", None)
    if payments is not None:
        return {
            "count": len(payments),
            "fields": sorted({key for row in payments for key in row.raw}),
            "declared_pagination": getattr(value, "has_declared_pagination", False),
        }
    return {"fields": sorted(getattr(value, "raw", {}))}


async def _create_probe(
    adapter: SteadfastAdapter,
    credentials: SteadfastCredentials,
    args: argparse.Namespace,
    report: SmokeReport,
) -> None:
    """Create one real parcel. Guarded twice, and refuses without a fixture."""
    if not (args.recipient_name and args.recipient_phone and args.recipient_address):
        report.add(
            SmokeResult(
                name="create parcel",
                ok=False,
                detail=(
                    "--allow-create needs --recipient-name, --recipient-phone and "
                    "--recipient-address. There is deliberately no default: a stray "
                    "invocation must not be able to ship to anyone."
                ),
            )
        )
        return

    # The confirmation already happened, synchronously, in main(). A blocking
    # prompt inside async code stalls the event loop, and this is the one place
    # in the program where stopping to ask a human is the entire point.
    invoice = args.create_invoice
    if not invoice:
        report.add(
            SmokeResult(
                name="create parcel", ok=True, skipped=True, detail="cancelled at the prompt"
            )
        )
        return

    from app.couriers.steadfast.dto import CreateOrderRequest

    request = CreateOrderRequest(
        invoice=invoice,
        recipient_name=args.recipient_name,
        recipient_phone=args.recipient_phone,
        recipient_address=args.recipient_address,
        cod_amount=int(args.cod_amount),
        note="ecomsbd operator smoke test",
        item_description="Smoke test",
    )
    try:
        call = await adapter.client.create_order(credentials, request)
        response = call.value
        report.add(
            SmokeResult(
                name="create parcel",
                ok=True,
                detail=(
                    f"created consignment {response.consignment_id}, "
                    f"tracking {response.tracking_code}"
                ),
                observed={
                    "invoice": response.invoice,
                    "provider_status": response.provider_status,
                    "fields": sorted(response.raw),
                },
            )
        )
    except SteadfastError as exc:
        report.add(
            SmokeResult(
                name="create parcel",
                ok=False,
                detail=f"{exc.kind}: {exc}",
                observed={
                    **exc.as_log_context(),
                    # If this was ambiguous, the parcel may exist under this
                    # invoice. Say so loudly — the operator has to check.
                    "invoice_to_check": invoice if exc.create_may_have_succeeded else None,
                },
            )
        )


def _confirm_create(args: argparse.Namespace) -> str | None:
    """Ask the operator to type the invoice. Returns it, or ``None`` to skip.

    Typing the invoice rather than "y" is deliberate: it cannot be done by
    reflex, and the thing being typed is the exact reference the real parcel
    will carry.
    """
    invoice = args.invoice or f"SMOKE-{utc_now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    if args.yes:
        return invoice
    prompt = "\n".join(
        [
            "",
            "This will create a REAL parcel at Steadfast.",
            f"  invoice:   {invoice}",
            f"  recipient: {args.recipient_name}",
            f"  cod:       {args.cod_amount} BDT",
            "Type the invoice to confirm: ",
        ]
    )
    return invoice if input(prompt).strip() == invoice else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.provider_smoke",
        description=(
            "Operator smoke test against a live courier provider. "
            "Read-only unless --allow-create is given."
        ),
    )
    parser.add_argument("provider", choices=["steadfast"])

    source = parser.add_argument_group("credential source (never on the command line)")
    source.add_argument("--account-id", help="Courier account UUID to read credentials from")
    source.add_argument("--tenant-id", help="Shop UUID, when the account id is not known")
    source.add_argument(
        "--env-credentials",
        action="store_true",
        help=f"Use {ENV_API_KEY} and {ENV_SECRET_KEY} instead of a stored account",
    )

    lookups = parser.add_argument_group("optional read checks")
    lookups.add_argument("--invoice", help="Check status by seller invoice")
    lookups.add_argument("--consignment-id", help="Check status by provider consignment id")
    lookups.add_argument("--tracking-code", help="Check status by tracking code")
    lookups.add_argument(
        "--probe-undocumented",
        action="store_true",
        help=(
            "Call the endpoints with no documented schema and record the field "
            "names they actually return"
        ),
    )

    create = parser.add_argument_group("creating a real parcel (guarded)")
    create.add_argument(
        "--allow-create",
        action="store_true",
        help="Permit creating one REAL parcel. Requires a recipient fixture and a confirmation.",
    )
    create.add_argument("--recipient-name")
    create.add_argument("--recipient-phone", help="11-digit Bangladeshi number")
    create.add_argument("--recipient-address")
    create.add_argument("--cod-amount", type=int, default=0, help="Taka, default 0")
    create.add_argument(
        "--yes", action="store_true", help="Skip the typed confirmation (unattended runs)"
    )

    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    parser.add_argument(
        "--allow-ci",
        action="store_true",
        help="Permit running with CI=true. CI must not depend on provider uptime.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if os.environ.get("CI", "").lower() in ("1", "true", "yes") and not args.allow_ci:
        print(
            "Refusing to run in CI. Continuous integration must never depend on a "
            "courier being up, and a live create here would ship a real parcel. "
            "Pass --allow-ci if this is a deliberate, separately-scheduled job.",
            file=sys.stderr,
        )
        return 2

    # The typed confirmation happens here, before any event loop starts.
    args.create_invoice = _confirm_create(args) if args.allow_create else None

    settings = get_settings()
    configure_logging(level="WARNING", json_output=False)
    set_context(RequestContext(trace_id=uuid.uuid4().hex, actor_type=ActorType.SYSTEM))

    import app.models  # noqa: F401  (registers every mapper)
    from app.db.tenancy import install_tenancy_guards

    install_tenancy_guards()

    report = asyncio.run(run_smoke(args))

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, default=str))
    else:
        print(report.render())

    if settings.app_env.is_production:
        print(
            "\nNote: this ran against the production environment's configuration.",
            file=sys.stderr,
        )
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
