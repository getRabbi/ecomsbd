"""Opt-in aggregate policy and immutable anonymous monthly benchmarks."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime, JSONColumn

revision = "a23005"
down_revision = "a23003"
branch_labels = None
depends_on = None


def _base():
    return [
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "network_preferences",
        *_base(),
        sa.Column("opted_in", sa.Boolean(), nullable=False),
        sa.Column("opted_in_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id"),
    )
    op.create_index("ix_network_preferences_tenant_id", "network_preferences", ["tenant_id"])
    op.create_index("ix_network_preferences_created_at", "network_preferences", ["created_at"])
    op.create_table(
        "network_benchmarks",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("period", sa.String(7), nullable=False, unique=True),
        sa.Column("policy_version", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("facts", JSONColumn, nullable=False),
    )
    op.create_index("ix_network_benchmarks_created_at", "network_benchmarks", ["created_at"])
    db = op.get_bind()
    if db.dialect.name == "postgresql":
        roles = (
            db.execute(
                sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
            )
            .scalars()
            .all()
        )
        grantees = ", ".join(["PUBLIC", *['"' + role + '"' for role in roles]])
        for table in ["network_preferences", "network_benchmarks"]:
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("network_benchmarks")
    op.drop_table("network_preferences")
