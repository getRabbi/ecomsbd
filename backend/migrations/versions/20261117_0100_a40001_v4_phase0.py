"""V4 phase 0: product_events (server-side funnel events)."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, JSONColumn, TZDateTime

revision = "a40001"
down_revision = "a39001"
branch_labels = None
depends_on = None

_TABLES = ["product_events"]


def upgrade():
    op.create_table(
        "product_events",
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("user_id", GUID, nullable=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("occurred_at", TZDateTime(), nullable=False),
        sa.Column("props", JSONColumn, nullable=False),
        sa.UniqueConstraint("tenant_id", "name", "dedupe_key", name="uq_product_events_dedupe"),
    )
    op.create_index("ix_product_events_tenant_id", "product_events", ["tenant_id"])
    op.create_index("ix_product_events_created_at", "product_events", ["created_at"])
    op.create_index("ix_product_events_name_occurred", "product_events", ["name", "occurred_at"])

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
        for table in _TABLES:
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    for table in reversed(_TABLES):
        op.drop_table(table)
