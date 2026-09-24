"""Two-way sync: catalog links, sync conflicts, per-order store state, outbound ops."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime, JSONColumn

revision = "a32001"
down_revision = "a31001"
branch_labels = None
depends_on = None

_TABLES = ["integration_links", "integration_conflicts", "integration_order_states"]


def _base():
    return [
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
    ]


def _connection():
    return sa.ForeignKey("integration_connections.id")


def _indexes(table):
    op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    op.create_index(f"ix_{table}_created_at", table, ["created_at"])


def upgrade():
    op.create_table(
        "integration_links",
        *_base(),
        sa.Column("connection_id", GUID, _connection(), nullable=False),
        sa.Column("external_key", sa.String(200), nullable=False),
        sa.Column("external_product_id", sa.String(100), nullable=False),
        sa.Column("external_variant_id", sa.String(100), nullable=True),
        sa.Column("external_inventory_id", sa.String(100), nullable=True),
        sa.Column("external_sku", sa.String(100), nullable=True),
        sa.Column("external_title", sa.String(300), nullable=False),
        sa.Column("external_price_paisa", sa.Integer(), nullable=True),
        sa.Column("external_tracked", sa.Boolean(), nullable=False),
        sa.Column("external_qty", sa.Integer(), nullable=True),
        sa.Column("external_seen_at", TZDateTime(), nullable=True),
        sa.Column("product_id", GUID, sa.ForeignKey("products.id"), nullable=True),
        sa.Column("variant_id", GUID, sa.ForeignKey("product_variants.id"), nullable=True),
        sa.Column("internal_key", sa.String(80), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("match_source", sa.String(16), nullable=True),
        sa.Column("synced_qty", sa.Integer(), nullable=True),
        sa.Column("local_basis", sa.Integer(), nullable=True),
        sa.Column("pending_since", TZDateTime(), nullable=True),
        sa.Column("last_synced_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "connection_id", "external_key"),
        sa.UniqueConstraint("tenant_id", "connection_id", "internal_key"),
    )
    _indexes("integration_links")
    op.create_index("ix_integration_link_state", "integration_links", ["connection_id", "state"])
    op.create_table(
        "integration_conflicts",
        *_base(),
        sa.Column("connection_id", GUID, _connection(), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("entity", sa.String(16), nullable=False),
        sa.Column("link_id", GUID, sa.ForeignKey("integration_links.id"), nullable=True),
        sa.Column("order_id", GUID, sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("detail", JSONColumn, nullable=False),
        sa.Column("recommended", sa.String(40), nullable=False),
        sa.Column("options", JSONColumn, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("open_key", sa.String(200), nullable=True),
        sa.Column("resolution", sa.String(40), nullable=True),
        sa.Column("resolved_by", GUID, nullable=True),
        sa.Column("resolved_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "connection_id", "open_key"),
    )
    _indexes("integration_conflicts")
    op.create_index(
        "ix_integration_conflict_state", "integration_conflicts", ["connection_id", "status"]
    )
    op.create_table(
        "integration_order_states",
        *_base(),
        sa.Column("connection_id", GUID, _connection(), nullable=False),
        sa.Column("order_id", GUID, sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("external_order_id", sa.String(200), nullable=False),
        sa.Column("provider_status", sa.String(40), nullable=True),
        sa.Column("pushed_status", sa.String(40), nullable=True),
        sa.Column("fulfillment_ref", sa.String(200), nullable=True),
        sa.Column("carrier", sa.String(60), nullable=True),
        sa.Column("tracking_code", sa.String(120), nullable=True),
        sa.UniqueConstraint("tenant_id", "connection_id", "order_id"),
    )
    _indexes("integration_order_states")
    with op.batch_alter_table("integration_events") as batch:
        batch.add_column(sa.Column("operation", sa.String(32), nullable=True))
        batch.add_column(sa.Column("payload", JSONColumn, nullable=True))
        batch.add_column(sa.Column("result_ref", sa.String(200), nullable=True))
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
    with op.batch_alter_table("integration_events") as batch:
        batch.drop_column("result_ref")
        batch.drop_column("payload")
        batch.drop_column("operation")
    for table in reversed(_TABLES):
        op.drop_table(table)
