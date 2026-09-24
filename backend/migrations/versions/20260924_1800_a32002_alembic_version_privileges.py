"""Client roles lose access to Alembic's version table.

Alembic creates `alembic_version` itself, before `d73e9c5a1201` revoked the
client roles' default privileges, so on Supabase `anon` and `authenticated` kept
the platform's default grants on it and could read or rewrite the schema
revision through the Data API. Only the database owner (migrations, readiness)
and roles that bypass row-level security need it, and neither is affected.
"""

from alembic import op
import sqlalchemy as sa

revision = "a32002"
down_revision = "a32001"
branch_labels = None
depends_on = None


def upgrade():
    db = op.get_bind()
    if db.dialect.name != "postgresql":
        return
    roles = (
        db.execute(
            sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
        )
        .scalars()
        .all()
    )
    grantees = ", ".join(["PUBLIC", *['"' + role + '"' for role in roles]])
    op.execute(f"REVOKE ALL ON TABLE public.alembic_version FROM {grantees}")
    op.execute("ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY")


def downgrade():
    # A rollback must never hand client roles the schema revision back.
    pass
