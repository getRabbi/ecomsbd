"""External source identities and durable ingestion receipts."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime, JSONColumn

revision = "a23002"
down_revision = "a23001"
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
        "external_order_sources",
        *_base(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("mapping", JSONColumn, nullable=False),
    )
    op.create_index("ix_external_order_sources_tenant_id", "external_order_sources", ["tenant_id"])
    op.create_index(
        "ix_external_order_sources_created_at", "external_order_sources", ["created_at"]
    )
    op.create_table(
        "external_orders",
        *_base(),
        sa.Column("source_id", GUID, sa.ForeignKey("external_order_sources.id"), nullable=False),
        sa.Column("external_order_id", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("order_id", GUID, sa.ForeignKey("orders.id"), nullable=False),
        sa.UniqueConstraint("tenant_id", "source_id", "external_order_id"),
    )
    op.create_index("ix_external_orders_tenant_id", "external_orders", ["tenant_id"])
    op.create_index("ix_external_orders_created_at", "external_orders", ["created_at"])
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
        for table in ["external_order_sources", "external_orders"]:
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("external_orders")
    op.drop_table("external_order_sources")
