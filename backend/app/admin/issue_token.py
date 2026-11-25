"""Issue a per-operator admin token (a ``platform_admins`` row).

The normal admin path (``app.admin.auth``): each operator holds their own token,
stored only as a keyed hash, with a role, and revocable on its own. Until this
command existed the only way in was the shared ``ADMIN_API_TOKENS`` bootstrap,
which audits as ``bootstrap:<n>`` and cannot be revoked without a redeploy.

Run it where the deployment's own configuration is loaded (the API container's
shell), because the hash is keyed from that environment's ``JWT_SIGNING_KEY``::

    python -m app.admin.issue_token --label rabbi --role SUPERADMIN

The token is printed once, on stdout, and never stored or logged; everything
else goes to stderr. Lose it and issue a new one (then revoke the old row:
``docs/runbooks/CREDENTIAL_LEAK.md``). Issuing is audited as
``admin.token_issued`` with the label and role, never the token.

``SUPPORT`` is accepted as well: it is the third role in the matrix and the
safest one to hand out.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import timedelta

import sqlalchemy as sa

from app.admin.models import PlatformAdmin, PlatformAdminRole
from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.core.config import Settings, get_settings
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.security import SecretHasher

__all__ = ["TOKEN_BYTES", "IssuedToken", "issue_admin_token", "main"]

#: 48 random bytes, URL-safe: 64 characters, well over the 32 the bootstrap
#: tokens must have, and the same generator the setup guide recommends.
TOKEN_BYTES = 48
#: Labels name a person (``rabbi``), not a secret. Kept short so the audit
#: trail and the console stay readable; the column holds 120.
MAX_LABEL = 80


@dataclass(frozen=True, slots=True)
class IssuedToken:
    admin_id: uuid.UUID
    label: str
    role: PlatformAdminRole
    token: str


def _clean_label(label: str) -> str:
    cleaned = label.strip()
    if not cleaned:
        raise ValueError("--label must not be empty")
    if len(cleaned) > MAX_LABEL:
        raise ValueError(f"--label is longer than {MAX_LABEL} characters")
    if cleaned.lower().startswith("bootstrap:"):
        # The bootstrap principal's label. A row named like it would make the
        # audit trail unable to tell the shared credential from a person.
        raise ValueError("--label must not start with 'bootstrap:'")
    return cleaned


async def issue_admin_token(
    *,
    label: str,
    role: PlatformAdminRole | str,
    settings: Settings | None = None,
    expires_in_days: int | None = None,
) -> IssuedToken:
    """Create a ``platform_admins`` row and return its token (shown once).

    Refuses a label that an *active* row already uses: two live credentials
    under one name would make "who did this" unanswerable. A revoked row's
    label may be reused, which is exactly the rotation case.
    """
    from app.db.session import system_session

    settings = settings or get_settings()
    cleaned = _clean_label(label)
    parsed_role = PlatformAdminRole(str(role).upper())
    if expires_in_days is not None and expires_in_days <= 0:
        raise ValueError("--expires-in-days must be positive")

    token = secrets.token_urlsafe(TOKEN_BYTES)
    token_hash = SecretHasher(settings).token_hash(token)

    async with system_session("admin cli: issue platform admin token") as session:
        clash = await session.scalar(
            sa.select(sa.func.count())
            .select_from(PlatformAdmin)
            .where(
                PlatformAdmin.label == cleaned,
                PlatformAdmin.is_active.is_(True),
                PlatformAdmin.revoked_at.is_(None),
            )
        )
        if clash:
            raise ValueError(
                f"An active admin token labelled {cleaned!r} already exists; "
                "revoke it first or choose another label"
            )

        row = PlatformAdmin(
            label=cleaned,
            role=str(parsed_role),
            token_hash=token_hash,
            is_active=True,
            expires_at=(utc_now() + timedelta(days=expires_in_days) if expires_in_days else None),
        )
        session.add(row)
        await session.flush()
        await record_audit(
            session,
            AuditAction.ADMIN_TOKEN_ISSUED,
            entity_type="platform_admin",
            entity_id=row.id,
            context={
                "label": cleaned,
                "role": str(parsed_role),
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "via": "cli",
            },
            actor_type=ActorType.SYSTEM,
        )
        admin_id = row.id

    return IssuedToken(admin_id=admin_id, label=cleaned, role=parsed_role, token=token)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.admin.issue_token",
        description=(
            "Create a platform_admins row and print its X-Admin-Token once. "
            "Run with the target deployment's configuration loaded."
        ),
    )
    parser.add_argument("--label", required=True, help="who holds it, e.g. 'rabbi'")
    parser.add_argument(
        "--role",
        required=True,
        type=str.upper,
        choices=[str(role) for role in PlatformAdminRole],
        help="SUPERADMIN, OPS or SUPPORT",
    )
    parser.add_argument(
        "--expires-in-days",
        type=int,
        default=None,
        help="optional expiry; omitted = no expiry (revoke to end it)",
    )
    return parser


async def _run(args: argparse.Namespace) -> IssuedToken:
    from app.db.session import dispose_engine

    try:
        return await issue_admin_token(
            label=args.label, role=args.role, expires_in_days=args.expires_in_days
        )
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from app.core.logging import configure_logging

    # No INFO lines: the only thing on stdout is the token.
    configure_logging(level="WARNING", json_output=False)
    token = set_context(RequestContext(trace_id=uuid.uuid4().hex, actor_type=ActorType.SYSTEM))
    try:
        import app.models  # noqa: F401  (registers every mapper)
        from app.db.tenancy import install_tenancy_guards

        install_tenancy_guards()
        issued = asyncio.run(_run(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        clear_context(token)

    settings = get_settings()
    print(
        f"Issued {issued.role} admin token for {issued.label!r} "
        f"(platform_admins {issued.admin_id}, env {settings.app_env}). "
        "It is shown once and stored only as a hash. Send it as the X-Admin-Token header.",
        file=sys.stderr,
    )
    print(issued.token)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
