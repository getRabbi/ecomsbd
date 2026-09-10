"""Verify a restored ecomsbd database.

Master spec section 48: *"do not claim backup safety unless restore has been
tested."* A dump that restores without an error but is missing a day of ledger
entries is a failed backup that looks like a successful one, so this runs after
every restore and re-checks the things that would make the copy useless:

1.  **Schema is at head.** A restore into an older schema silently loses
    whatever the newest migration added.
2.  **Every expected table exists.** Compared against the model registry, not a
    hand-maintained list.
3.  **The money invariants hold** (section 81.10). Outstanding receivables and
    the ledger's receivable bucket are derived by two different routes; if the
    restored copy disagrees with itself, it is not a usable backup.
4.  **No settlement exceeds what was owed** (section 17.2).
5.  **PII is still encrypted.** A restore that decrypted anything, or that was
    taken from a database whose encryption had failed, is an incident.

Run it directly against a *restored* database:

    DATABASE_URL=postgresql+asyncpg://... python infra/backup/verify_restore.py

Exit code 0 means the restore is usable. Anything else means it is not, and the
drill has failed — which is the outcome worth knowing about *before* an outage.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402


def _normalise(url: str) -> str:
    """Accept a libpq URL and drive it with asyncpg."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Failure(Exception):
    """A check that means the restored copy is not usable."""


def _check_schema_head(conn: sa.Connection) -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()

    applied = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    if applied != head:
        raise Failure(
            f"the restored database is at migration {applied}, not head {head}. "
            "The backup predates a schema change, or the restore was partial."
        )
    return str(applied)


def _check_tables(conn: sa.Connection) -> int:
    import app.models  # noqa: F401
    from app.db.base import Base

    present = set(sa.inspect(conn).get_table_names())
    expected = set(Base.metadata.tables)
    missing = expected - present
    if missing:
        raise Failure(f"tables missing from the restored database: {sorted(missing)}")
    return len(expected)


def _check_money_invariants(conn: sa.Connection) -> dict[str, int]:
    """Section 81.10, per tenant, on the restored copy.

    The collectible statuses come from the model rather than being written out
    here. A hand-typed list in a verification script is a list that goes stale
    the first time the enum gains a member — and a verification script that
    reports a false failure at 3am is worse than no verification script, because
    the next real failure will be assumed to be another false one.
    """
    from app.money.models import ReceivableStatus

    collectible = [str(s) for s in ReceivableStatus if s.is_collectible]
    receivable_rows = conn.execute(
        sa.text(
            """
            SELECT tenant_id,
                   COALESCE(SUM(
                     GREATEST(0, collectible_paisa + adjustment_paisa
                                 - settled_paisa - deduction_paisa)
                   ), 0) AS outstanding
              FROM cod_receivables
             WHERE status = ANY(:collectible)
             GROUP BY tenant_id
            """
        ),
        {"collectible": collectible},
    ).all()

    ledger_rows = conn.execute(
        sa.text(
            """
            SELECT tenant_id,
                   COALESCE(SUM(CASE WHEN direction = 'CREDIT'
                                     THEN amount_paisa ELSE -amount_paisa END), 0) AS net
              FROM financial_ledger_entries
             WHERE bucket = 'COD_RECEIVABLE'
             GROUP BY tenant_id
            """
        )
    ).all()

    outstanding = {str(row[0]): int(row[1]) for row in receivable_rows}
    ledger = {str(row[0]): int(row[1]) for row in ledger_rows}

    mismatched = [
        tenant
        for tenant in set(outstanding) | set(ledger)
        if outstanding.get(tenant, 0) != ledger.get(tenant, 0)
    ]
    if mismatched:
        raise Failure(
            f"{len(mismatched)} tenant(s) whose receivables and ledger disagree in "
            f"the restored copy, e.g. {mismatched[:3]}. This backup is not usable."
        )

    oversettled = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM cod_receivables
             WHERE settled_paisa > collectible_paisa + adjustment_paisa
            """
        )
    ).scalar_one()
    if int(oversettled):
        raise Failure(f"{oversettled} receivable(s) settled beyond what was owed")

    return {"tenants_checked": len(set(outstanding) | set(ledger))}


def _check_pii_is_encrypted(conn: sa.Connection) -> int:
    """A phone number in clear text in a dump is an incident, not a backup."""
    leaked = conn.execute(
        sa.text(
            r"""
            SELECT COUNT(*) FROM customers
             WHERE phone_enc <> ''
               AND phone_enc ~ '^\+?8?8?0?1[3-9][0-9]{8}$'
            """
        )
    ).scalar_one()
    if int(leaked):
        raise Failure(f"{leaked} customer phone(s) are stored unencrypted")

    total = conn.execute(sa.text("SELECT COUNT(*) FROM customers")).scalar_one()
    return int(total)


def _run_checks(sync_conn: sa.Connection) -> dict[str, object]:
    results: dict[str, object] = {}
    results["migration_head"] = _check_schema_head(sync_conn)
    results["tables"] = _check_tables(sync_conn)
    results["rows"] = {
        table: int(
            sync_conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608
        )
        for table in (
            "tenants",
            "users",
            "orders",
            "customers",
            "cod_receivables",
            "financial_ledger_entries",
            "payouts",
            "profit_snapshots",
            "subscriptions",
        )
    }
    results.update(_check_money_invariants(sync_conn))
    results["customers_checked"] = _check_pii_is_encrypted(sync_conn)
    return results


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required and must point at the RESTORED database")
        return 2

    engine = create_async_engine(_normalise(url))
    try:
        async with engine.connect() as conn:
            results = await conn.run_sync(_run_checks)
    except Failure as failure:
        print(f"RESTORE VERIFICATION FAILED: {failure}")
        return 1
    finally:
        await engine.dispose()

    print("RESTORE VERIFICATION PASSED")
    print(f"  migration head : {results['migration_head']}")
    print(f"  tables present : {results['tables']}")
    print(f"  tenants checked: {results['tenants_checked']}")
    for table, count in results["rows"].items():  # type: ignore[union-attr]
        print(f"  {table:<28} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
