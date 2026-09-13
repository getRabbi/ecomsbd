"""Alembic environment.

The database URL is read from application settings rather than ``alembic.ini``,
so migrations always target the same database the application reads. A second
copy of the connection string is a good way to migrate the wrong environment.

Migrations are authored to run on both PostgreSQL (production) and SQLite (test
and local), which is what lets the migration-sanity suite execute in CI without
a database server. The PostgreSQL rendering is always the richer one — native
``uuid``, ``jsonb``, ``timestamptz`` — via the variants in ``app.db.types``.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings

# Importing the registry makes every table visible to autogenerate.
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# A controlled production migration needs database access, not OAuth/email
# credentials. This explicit mode is never used by API/worker startup and never
# changes their production safety checks. It accepts only PostgreSQL/asyncpg.
if context.get_x_argument(as_dictionary=True).get("database-only") == "true":
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or make_url(database_url).drivername != "postgresql+asyncpg":
        raise RuntimeError("DATABASE_URL must use postgresql+asyncpg for database-only migrations")
else:
    database_url = get_settings().database_url
# ConfigParser treats '%' specially; URL-encoded passwords must survive intact.
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Skip Alembic's own bookkeeping table."""
    return not (type_ == "table" and name == "alembic_version")


def _render_item(type_, obj, autogen_context):
    """Render the project's custom column types as importable expressions.

    Autogenerate renders a ``TypeDecorator`` by repr, which produces code
    referencing modules the migration does not import, and renders a
    ``with_variant`` type with an unqualified ``Text()``. Both fail at import.
    Emitting the module-level aliases instead keeps generated migrations valid
    and keeps the PostgreSQL/SQLite variants in one place.
    """
    import sqlalchemy as sa

    from app.db.types import TZDateTime

    if type_ != "type":
        return False

    if isinstance(obj, TZDateTime):
        autogen_context.imports.add("import app.db.types")
        return "app.db.types.TZDateTime()"

    if isinstance(obj, sa.JSON) and getattr(obj, "_variant_mapping", None):
        autogen_context.imports.add("import app.db.types")
        return "app.db.types.JSONColumn"

    return False


def run_migrations_offline() -> None:
    """Emit SQL without connecting. Used to review a migration before it runs."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        render_item=_render_item,
        # Required on SQLite: it cannot ALTER most columns in place.
        render_as_batch=database_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        render_item=_render_item,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
