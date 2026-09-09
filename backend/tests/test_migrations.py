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
