"""Migration sanity.

Master spec section 109: every schema change ships as an Alembic migration, and
a migration that does not apply cleanly is a broken deploy.

These tests assert three things:

*   the migration chain is linear and applies from empty to head;
*   downgrade actually reverses it, so a rollback is real rather than aspirational;
*   the models and the migrations agree — no drift that would surface as a
    production ``UndefinedColumn`` after a deploy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


class TestMigrationChain:
    def test_there_is_exactly_one_head(self, settings) -> None:
        # More than one head means two branches shipped independently and the
        # next deploy would apply an arbitrary one of them.
        script = ScriptDirectory.from_config(_alembic_config(settings.database_url))
        assert len(script.get_heads()) == 1

    def test_every_revision_has_a_docstring(self, settings) -> None:
        script = ScriptDirectory.from_config(_alembic_config(settings.database_url))
        for revision in script.walk_revisions():
            assert revision.doc, f"{revision.revision} has no description"

    def test_upgrade_then_downgrade_round_trips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Apply the whole chain to an empty database, then reverse it.

        ``migrations/env.py`` deliberately takes the URL from application
        settings rather than ``alembic.ini`` — a second copy of the connection
        string is how the wrong environment gets migrated — so this test points
        the settings at a throwaway file instead of overriding the ini option.
        """
        from app.core.config import get_settings, reset_settings_cache

        database_file = tmp_path / "roundtrip.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_file}")
        reset_settings_cache()
        try:
            config = _alembic_config(get_settings().database_url)

            command.upgrade(config, "head")
            engine = sa.create_engine(f"sqlite:///{database_file}")
            with engine.connect() as conn:
                after_upgrade = set(sa.inspect(conn).get_table_names())
            assert "tenants" in after_upgrade
            assert "outbox_events" in after_upgrade

            command.downgrade(config, "base")
            with engine.connect() as conn:
                after_downgrade = set(sa.inspect(conn).get_table_names())
            assert after_downgrade <= {"alembic_version"}
            engine.dispose()
        finally:
            monkeypatch.undo()
            reset_settings_cache()


class TestSchemaMatchesModels:
    async def test_no_drift_between_models_and_migrations(self, db: AsyncSession, settings) -> None:
        """The applied schema must contain every mapped table and column."""
        import app.models  # noqa: F401
        from app.db.base import Base

        connection = await db.connection()
        inspector = await connection.run_sync(lambda sync_conn: sa.inspect(sync_conn))
        actual_tables = set(await connection.run_sync(lambda c: sa.inspect(c).get_table_names()))

        expected_tables = set(Base.metadata.tables)
        missing = expected_tables - actual_tables
        assert not missing, f"tables declared on models but absent from migrations: {missing}"

        for table_name, table in Base.metadata.tables.items():
            actual_columns = {
                column["name"]
                for column in await connection.run_sync(
                    lambda c, name=table_name: sa.inspect(c).get_columns(name)
                )
            }
            expected_columns = {column.name for column in table.columns}
            missing_columns = expected_columns - actual_columns
            assert not missing_columns, (
                f"{table_name} is missing {sorted(missing_columns)}; "
                "generate a migration for the model change"
            )
        del inspector


class TestMoneyColumnTypes:
    def test_no_floating_point_column_exists_anywhere(self) -> None:
        """Master spec sections 17.5 and 32: money is never a float.

        Checked across the whole schema rather than a list of known money
        columns, so a future ``Float`` slipping into any table fails here.
        """
        import app.models  # noqa: F401
        from app.db.base import Base

        offenders = [
            f"{table_name}.{column.name}"
            for table_name, table in Base.metadata.tables.items()
            for column in table.columns
            if isinstance(column.type, (sa.Float, sa.Numeric))
            and not isinstance(column.type, sa.Integer)
        ]
        assert not offenders, f"floating-point columns are forbidden: {offenders}"

    def test_timestamps_are_timezone_aware(self) -> None:
        """Master spec section 69: timestamps are stored in UTC, aware."""
        import app.models  # noqa: F401
        from app.db.base import Base
        from app.db.types import TZDateTime

        naive = [
            f"{table_name}.{column.name}"
            for table_name, table in Base.metadata.tables.items()
            for column in table.columns
            if isinstance(column.type, sa.DateTime) and not isinstance(column.type, TZDateTime)
        ]
        assert not naive, f"use TZDateTime for: {naive}"


class TestConstraints:
    @pytest.mark.parametrize(
        ("table", "columns"),
        [
            ("users", {"phone_search_hmac"}),
            ("refresh_tokens", {"token_hash"}),
            ("tenant_users", {"tenant_id", "user_id"}),
            ("idempotency_keys", {"tenant_id", "key", "endpoint"}),
            ("outbox_events", {"dedupe_key"}),
        ],
    )
    def test_uniqueness_is_enforced_by_the_database(self, table: str, columns: set[str]) -> None:
        """Application validation is not enough (master spec section 108)."""
        import app.models  # noqa: F401
        from app.db.base import Base

        constraints = Base.metadata.tables[table].constraints
        unique_sets = [
            {column.name for column in constraint.columns}
            for constraint in constraints
            if isinstance(constraint, sa.UniqueConstraint)
        ]
        assert columns in unique_sets, f"{table} needs a unique constraint on {columns}"


class TestAlembicVersionPrivileges:
    """Supabase grants client roles everything Alembic created before d73e9c5a1201."""

    @staticmethod
    def _supabase_grants_then_upgrade(sync_conn: sa.Connection) -> dict[str, object]:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        for role in ("anon", "authenticated"):
            sync_conn.execute(
                sa.text(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = "
                    f"'{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"
                )
            )
        sync_conn.execute(sa.text("ALTER TABLE public.alembic_version DISABLE ROW LEVEL SECURITY"))
        sync_conn.execute(
            sa.text("GRANT ALL ON TABLE public.alembic_version TO PUBLIC, anon, authenticated")
        )
        script = ScriptDirectory.from_config(_alembic_config("postgresql://unused"))
        module = script.get_revision("a32002").module
        with Operations.context(MigrationContext.configure(sync_conn)):
            module.upgrade()

        def allowed(role: str, privilege: str) -> bool:
            return bool(
                sync_conn.execute(
                    sa.text("SELECT has_table_privilege(:role, 'public.alembic_version', :p)"),
                    {"role": role, "p": privilege},
                ).scalar_one()
            )

        return {
            "client": {
                (role, privilege): allowed(role, privilege)
                for role in ("anon", "authenticated")
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            },
            "public_acl": sync_conn.execute(
                sa.text(
                    "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a "
                    "WHERE c.oid = 'public.alembic_version'::regclass AND a.grantee = 0"
                )
            ).scalar_one(),
            "rls": sync_conn.execute(
                sa.text(
                    "SELECT relrowsecurity FROM pg_class "
                    "WHERE oid = 'public.alembic_version'::regclass"
                )
            ).scalar_one(),
            "owner_reads": sync_conn.execute(
                sa.text("SELECT version_num FROM public.alembic_version")
            ).scalar_one(),
        }

    @pytest.mark.postgres
    async def test_client_roles_cannot_read_or_rewrite_the_revision(self, system_db) -> None:
        if system_db.bind.dialect.name != "postgresql":
            pytest.skip("PostgreSQL only")
        connection = await system_db.connection()
        try:
            state = await connection.run_sync(self._supabase_grants_then_upgrade)
        finally:
            # Roles and grants are transactional: nothing leaks into later tests.
            await system_db.rollback()
        assert not any(state["client"].values()), state["client"]
        assert state["public_acl"] == 0
        assert state["rls"] is True
        # The owner (API readiness probe, `alembic upgrade`) bypasses RLS.
        head = ScriptDirectory.from_config(
            _alembic_config("postgresql://unused")
        ).get_current_head()
        assert state["owner_reads"] == head

    @pytest.mark.postgres
    async def test_migrated_database_keeps_the_version_table_private(self, system_db) -> None:
        if system_db.bind.dialect.name != "postgresql":
            pytest.skip("PostgreSQL only")
        rls = (
            await system_db.execute(
                sa.text(
                    "SELECT relrowsecurity FROM pg_class "
                    "WHERE oid = 'public.alembic_version'::regclass"
                )
            )
        ).scalar_one()
        public = (
            await system_db.execute(
                sa.text(
                    "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a "
                    "WHERE c.oid = 'public.alembic_version'::regclass AND a.grantee = 0"
                )
            )
        ).scalar_one()
        assert rls is True
        assert public == 0
