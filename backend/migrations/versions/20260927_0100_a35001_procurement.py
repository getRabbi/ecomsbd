"""Inventory + procurement: suppliers, purchase orders, receipts, payables, locations, transfers."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime

revision = "a35001"
down_revision = "a34001"
branch_labels = None
depends_on = None

_TABLES = [
    "suppliers",
    "supplier_items",
    "warehouses",
    "warehouse_stock",
    "purchase_orders",
    "purchase_order_lines",
    "goods_receipts",
    "goods_receipt_lines",
    "supplier_payments",
    "stock_transfers",
    "stock_transfer_lines",
]


def _base():
    return [
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
    ]


def _indexes(table):
    op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    op.create_index(f"ix_{table}_created_at", table, ["created_at"])


def _item():
    return [
        sa.Column("product_id", GUID, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("variant_id", GUID, sa.ForeignKey("product_variants.id"), nullable=True),
        sa.Column("item_key", sa.String(80), nullable=False),
    ]


def upgrade():
    op.create_table(
        "suppliers",
        *_base(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("name_key", sa.String(160), nullable=False),
        sa.Column("contact_name", sa.String(160), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", GUID, nullable=False),
        sa.UniqueConstraint("tenant_id", "name_key"),
    )
    _indexes("suppliers")
    op.create_table(
        "supplier_items",
        *_base(),
        sa.Column("supplier_id", GUID, sa.ForeignKey("suppliers.id"), nullable=False),
        *_item(),
        sa.Column("supplier_sku", sa.String(64), nullable=True),
        sa.Column("last_unit_cost_paisa", sa.BigInteger(), nullable=True),
        sa.Column("last_received_at", TZDateTime(), nullable=True),
        sa.Column("is_preferred", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "supplier_id", "item_key"),
    )
    _indexes("supplier_items")
    op.create_index("ix_supplier_items_item", "supplier_items", ["tenant_id", "item_key"])
    op.create_table(
        "warehouses",
        *_base(),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("code", sa.String(24), nullable=False),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code"),
    )
    _indexes("warehouses")
    op.create_table(
        "warehouse_stock",
        *_base(),
        sa.Column("warehouse_id", GUID, sa.ForeignKey("warehouses.id"), nullable=False),
        *_item(),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="warehouse_stock_non_negative"),
        sa.UniqueConstraint("tenant_id", "warehouse_id", "item_key"),
    )
    _indexes("warehouse_stock")
    op.create_table(
        "purchase_orders",
        *_base(),
        sa.Column("number", sa.String(24), nullable=False),
        sa.Column("supplier_id", GUID, sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("warehouse_id", GUID, sa.ForeignKey("warehouses.id"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expected_at", TZDateTime(), nullable=True),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("total_paisa", sa.BigInteger(), nullable=False),
        sa.Column("received_value_paisa", sa.BigInteger(), nullable=False),
        sa.Column("paid_paisa", sa.BigInteger(), nullable=False),
        sa.Column("payment_due_at", TZDateTime(), nullable=True),
        sa.Column("ordered_at", TZDateTime(), nullable=True),
        sa.Column("first_received_at", TZDateTime(), nullable=True),
        sa.Column("received_at", TZDateTime(), nullable=True),
        sa.Column("cancelled_at", TZDateTime(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_by", GUID, nullable=True),
        sa.Column("ordered_by", GUID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("paid_paisa >= 0", name="purchase_orders_paid_non_negative"),
        sa.UniqueConstraint("tenant_id", "number"),
    )
    _indexes("purchase_orders")
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["tenant_id", "status"])
    op.create_table(
        "purchase_order_lines",
        *_base(),
        sa.Column("purchase_order_id", GUID, sa.ForeignKey("purchase_orders.id"), nullable=False),
        *_item(),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("quantity_ordered", sa.Integer(), nullable=False),
        sa.Column("quantity_received", sa.Integer(), nullable=False),
        sa.Column("quantity_rejected", sa.Integer(), nullable=False),
        sa.Column("unit_cost_paisa", sa.BigInteger(), nullable=False),
        sa.Column("update_cost", sa.Boolean(), nullable=False),
        sa.CheckConstraint("quantity_ordered > 0", name="po_line_quantity_positive"),
        sa.UniqueConstraint("tenant_id", "purchase_order_id", "item_key"),
    )
    _indexes("purchase_order_lines")
    op.create_table(
        "goods_receipts",
        *_base(),
        sa.Column("purchase_order_id", GUID, sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("warehouse_id", GUID, sa.ForeignKey("warehouses.id"), nullable=True),
        sa.Column("received_at", TZDateTime(), nullable=False),
        sa.Column("supplier_reference", sa.String(120), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("actor_id", GUID, nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("accepted_units", sa.Integer(), nullable=False),
        sa.Column("rejected_units", sa.Integer(), nullable=False),
        sa.Column("value_paisa", sa.BigInteger(), nullable=False),
        sa.Column("over_receipt_reason", sa.String(300), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    _indexes("goods_receipts")
    op.create_table(
        "goods_receipt_lines",
        *_base(),
        sa.Column("receipt_id", GUID, sa.ForeignKey("goods_receipts.id"), nullable=False),
        sa.Column("line_id", GUID, sa.ForeignKey("purchase_order_lines.id"), nullable=False),
        sa.Column("quantity_accepted", sa.Integer(), nullable=False),
        sa.Column("quantity_rejected", sa.Integer(), nullable=False),
        sa.Column("reject_reason", sa.String(24), nullable=True),
        sa.Column("movement_id", GUID, sa.ForeignKey("stock_movements.id"), nullable=True),
    )
    _indexes("goods_receipt_lines")
    op.create_table(
        "supplier_payments",
        *_base(),
        sa.Column("purchase_order_id", GUID, sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("supplier_id", GUID, sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("amount_paisa", sa.BigInteger(), nullable=False),
        sa.Column("paid_at", TZDateTime(), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("actor_id", GUID, nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.CheckConstraint("amount_paisa > 0", name="supplier_payment_positive"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    _indexes("supplier_payments")
    op.create_table(
        "stock_transfers",
        *_base(),
        sa.Column("number", sa.String(24), nullable=False),
        sa.Column("from_warehouse_id", GUID, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("to_warehouse_id", GUID, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("actor_id", GUID, nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.UniqueConstraint("tenant_id", "number"),
    )
    _indexes("stock_transfers")
    op.create_table(
        "stock_transfer_lines",
        *_base(),
        sa.Column("transfer_id", GUID, sa.ForeignKey("stock_transfers.id"), nullable=False),
        *_item(),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="transfer_line_quantity_positive"),
    )
    _indexes("stock_transfer_lines")

    with op.batch_alter_table("stock_movements") as batch:
        batch.add_column(
            sa.Column(
                "warehouse_id",
                GUID,
                sa.ForeignKey("warehouses.id", name="fk_stock_movements_warehouse"),
                nullable=True,
            )
        )
    with op.batch_alter_table("automation_tasks") as batch:
        batch.alter_column("order_id", existing_type=GUID, nullable=True)
        batch.alter_column("customer_id", existing_type=GUID, nullable=True)
        batch.add_column(sa.Column("purchase_order_id", GUID, nullable=True))
        batch.add_column(sa.Column("product_id", GUID, nullable=True))
        batch.add_column(sa.Column("assignee_id", GUID, nullable=True))

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
    with op.batch_alter_table("automation_tasks") as batch:
        batch.drop_column("assignee_id")
        batch.drop_column("product_id")
        batch.drop_column("purchase_order_id")
    with op.batch_alter_table("stock_movements") as batch:
        batch.drop_constraint("fk_stock_movements_warehouse", type_="foreignkey")
        batch.drop_column("warehouse_id")
    for table in reversed(_TABLES):
        op.drop_table(table)
