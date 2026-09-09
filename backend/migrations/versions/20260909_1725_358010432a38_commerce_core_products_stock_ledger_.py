"""commerce core: products, stock ledger, customers, orders, consignment items, imports, sync

Phase B schema. Eleven tables, all tenant-owned:

*   products / stock_movements — catalogue plus the append-only
    inventory ledger (master spec sections 10.4, 20). Stock is never a bare
    number someone sets; products.stock_on_hand is a transactionally
    maintained cache of the movements.
*   customers / customer_addresses — the private per-seller CRM
    (section 21). The phone is stored encrypted plus a keyed HMAC for exact
    lookup and the last four digits (section 133); the raw address is kept as
    evidence and never overwritten by a provider's version (section 71).
*   orders / order_items — the commerce object and its lines, each line
    snapshotting the price and cost at order time so historical profit cannot be
    rewritten by a later product edit (section 18.2).
*   consignments / consignment_items — parcels and the per-item
    fulfilment mapping section 72 requires for partial delivery. Schema and
    state machine only; no courier is contacted until Phase C.
*   imports / import_rows — the generic import pipeline (section 98),
    retaining the source SHA256 and every raw row.
*   sync_mutations — the offline mutation ledger that makes a replayed
    device queue idempotent (sections 37, 38).

Additive only. No Phase A table is altered.

Revision ID: 358010432a38
Revises: aa2d022bb16b
Create Date: 2026-09-09 17:25:02.720364+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
import app.db.types

revision: str = "358010432a38"
down_revision: str | None = "aa2d022bb16b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("name", sa.String(length=160), nullable=True),
        sa.Column("phone_search_hmac", sa.String(length=64), nullable=False),
        sa.Column("phone_enc", sa.Text(), nullable=False),
        sa.Column("phone_last4", sa.String(length=4), nullable=False),
        sa.Column("phone_masked", sa.String(length=20), nullable=False),
        sa.Column("alt_phone_enc", sa.Text(), nullable=True),
        sa.Column("alt_phone_masked", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("flag", sa.String(length=16), nullable=False),
        sa.Column("flag_reason", sa.String(length=400), nullable=True),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("delivered_count", sa.Integer(), nullable=False),
        sa.Column("returned_count", sa.Integer(), nullable=False),
        sa.Column("cancelled_count", sa.Integer(), nullable=False),
        sa.Column("realized_revenue_paisa", sa.BigInteger(), nullable=False),
        sa.Column("first_order_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("last_order_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("metadata_json", app.db.types.JSONColumn, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("deleted_at", app.db.types.TZDateTime(), nullable=True),
        sa.CheckConstraint(
            "delivered_count >= 0", name=op.f("ck_customers_delivered_count_non_negative")
        ),
        sa.CheckConstraint("order_count >= 0", name=op.f("ck_customers_order_count_non_negative")),
        sa.CheckConstraint(
            "returned_count >= 0", name=op.f("ck_customers_returned_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_customers_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
        sa.UniqueConstraint(
            "tenant_id", "phone_search_hmac", name="uq_customers_tenant_id_phone_search_hmac"
        ),
    )
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_customers_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            "ix_customers_tenant_created_at", ["tenant_id", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_customers_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            "ix_customers_tenant_last_order", ["tenant_id", "last_order_at"], unique=False
        )
        batch_op.create_index(
            "ix_customers_tenant_phone", ["tenant_id", "phone_search_hmac"], unique=False
        )

    op.create_table(
        "imports",
        sa.Column("template", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("original_filename", sa.String(length=400), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=400), nullable=True),
        sa.Column("column_mapping", app.db.types.JSONColumn, nullable=False),
        sa.Column("detected_headers", app.db.types.JSONColumn, nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("ready_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("invalid_count", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("dry_run_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("committed_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("failure_reason", sa.String(length=400), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.CheckConstraint("row_count >= 0", name=op.f("ck_imports_row_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_imports_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_imports")),
    )
    with op.batch_alter_table("imports", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_imports_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_imports_status"), ["status"], unique=False)
        batch_op.create_index(
            "ix_imports_tenant_created", ["tenant_id", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_imports_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            "ix_imports_tenant_sha", ["tenant_id", "template", "source_sha256"], unique=False
        )
        batch_op.create_index("ix_imports_tenant_status", ["tenant_id", "status"], unique=False)

    op.create_table(
        "products",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cost_paisa", sa.BigInteger(), nullable=False),
        sa.Column("default_selling_price_paisa", sa.BigInteger(), nullable=False),
        sa.Column("stock_tracking_enabled", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("archived_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("image_storage_key", sa.String(length=400), nullable=True),
        sa.Column("stock_on_hand", sa.Integer(), nullable=False),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=True),
        sa.Column("attributes", app.db.types.JSONColumn, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.CheckConstraint("cost_paisa >= 0", name=op.f("ck_products_cost_non_negative")),
        sa.CheckConstraint(
            "default_selling_price_paisa >= 0", name=op.f("ck_products_selling_price_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_products_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_id_sku"),
    )
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_products_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            "ix_products_tenant_active_name", ["tenant_id", "is_active", "name"], unique=False
        )
        batch_op.create_index(
            "ix_products_tenant_created_at", ["tenant_id", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_products_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "sync_mutations",
        sa.Column("mutation_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("payload", app.db.types.JSONColumn, nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", app.db.types.JSONColumn, nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=400), nullable=True),
        sa.Column("client_timestamp", app.db.types.TZDateTime(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_sync_mutations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_mutations")),
        sa.UniqueConstraint(
            "tenant_id", "mutation_id", name="uq_sync_mutations_tenant_id_mutation_id"
        ),
    )
    with op.batch_alter_table("sync_mutations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sync_mutations_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index("ix_sync_mutations_device", ["device_id", "created_at"], unique=False)
        batch_op.create_index(
            "ix_sync_mutations_entity", ["tenant_id", "entity_type", "entity_id"], unique=False
        )
        batch_op.create_index(
            "ix_sync_mutations_tenant_created", ["tenant_id", "created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_sync_mutations_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "customer_addresses",
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("raw_address", sa.Text(), nullable=False),
        sa.Column("normalized_address", sa.Text(), nullable=True),
        sa.Column("label", sa.String(length=60), nullable=True),
        sa.Column("division", sa.String(length=80), nullable=True),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("area", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column("provider_location_refs", app.db.types.JSONColumn, nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_customer_addresses_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_customer_addresses_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_addresses")),
    )
    with op.batch_alter_table("customer_addresses", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_customer_addresses_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            "ix_customer_addresses_customer", ["customer_id", "is_default"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_customer_addresses_customer_id"), ["customer_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_customer_addresses_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "import_rows",
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw", app.db.types.JSONColumn, nullable=False),
        sa.Column("parsed", app.db.types.JSONColumn, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("errors", app.db.types.JSONColumn, nullable=False),
        sa.Column("warnings", app.db.types.JSONColumn, nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("created_entity_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["imports.id"],
            name=op.f("fk_import_rows_import_id_imports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_import_rows_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_rows")),
        sa.UniqueConstraint("import_id", "row_number", name="uq_import_rows_import_id_row_number"),
    )
    with op.batch_alter_table("import_rows", schema=None) as batch_op:
        batch_op.create_index(
            "ix_import_rows_fingerprint", ["tenant_id", "fingerprint"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_import_rows_import_id"), ["import_id"], unique=False)
        batch_op.create_index("ix_import_rows_import_status", ["import_id", "status"], unique=False)
        batch_op.create_index(batch_op.f("ix_import_rows_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "orders",
        sa.Column("order_number", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("customer_phone_hmac", sa.String(length=64), nullable=True),
        sa.Column("customer_name", sa.String(length=160), nullable=True),
        sa.Column("customer_phone_masked", sa.String(length=20), nullable=True),
        sa.Column("delivery_address_raw", sa.Text(), nullable=True),
        sa.Column("delivery_address_normalized", sa.Text(), nullable=True),
        sa.Column("delivery_district", sa.String(length=80), nullable=True),
        sa.Column("delivery_area", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("subtotal_paisa", sa.BigInteger(), nullable=False),
        sa.Column("discount_paisa", sa.BigInteger(), nullable=False),
        sa.Column("delivery_fee_paisa", sa.BigInteger(), nullable=False),
        sa.Column("cod_amount_paisa", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("packed_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("completed_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("cancelled_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_json", app.db.types.JSONColumn, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("deleted_at", app.db.types.TZDateTime(), nullable=True),
        sa.CheckConstraint("cod_amount_paisa >= 0", name=op.f("ck_orders_cod_non_negative")),
        sa.CheckConstraint(
            "delivery_fee_paisa >= 0", name=op.f("ck_orders_delivery_fee_non_negative")
        ),
        sa.CheckConstraint("discount_paisa >= 0", name=op.f("ck_orders_discount_non_negative")),
        sa.CheckConstraint("subtotal_paisa >= 0", name=op.f("ck_orders_subtotal_non_negative")),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_orders_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_orders_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("tenant_id", "client_id", name="uq_orders_tenant_id_client_id"),
        sa.UniqueConstraint("tenant_id", "order_number", name="uq_orders_tenant_id_order_number"),
    )
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_orders_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            "ix_orders_customer_created", ["customer_id", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_orders_customer_id"), ["customer_id"], unique=False)
        batch_op.create_index(
            "ix_orders_dup_scan", ["tenant_id", "customer_phone_hmac", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_orders_status"), ["status"], unique=False)
        batch_op.create_index(
            "ix_orders_tenant_business_date", ["tenant_id", "business_date"], unique=False
        )
        batch_op.create_index(
            "ix_orders_tenant_created_at", ["tenant_id", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_orders_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            "ix_orders_tenant_status_created", ["tenant_id", "status", "created_at"], unique=False
        )
        batch_op.create_index(
            "ix_orders_tenant_updated_at", ["tenant_id", "updated_at"], unique=False
        )

    op.create_table(
        "stock_movements",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("order_item_id", sa.Uuid(), nullable=True),
        sa.Column("consignment_id", sa.Uuid(), nullable=True),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=400), nullable=True),
        sa.Column("occurred_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "quantity_delta <> 0", name=op.f("ck_stock_movements_quantity_delta_non_zero")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_stock_movements_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_stock_movements_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_movements")),
    )
    with op.batch_alter_table("stock_movements", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_stock_movements_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index("ix_stock_movements_order_id", ["order_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_stock_movements_product_id"), ["product_id"], unique=False
        )
        batch_op.create_index(
            "ix_stock_movements_product_occurred", ["product_id", "occurred_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_stock_movements_reason"), ["reason"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_stock_movements_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            "ix_stock_movements_tenant_occurred", ["tenant_id", "occurred_at"], unique=False
        )

    op.create_table(
        "consignments",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_consignment_id", sa.String(length=120), nullable=True),
        sa.Column("tracking_code", sa.String(length=120), nullable=True),
        sa.Column("merchant_reference", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_raw_status", sa.String(length=120), nullable=True),
        sa.Column("cod_amount_paisa", sa.BigInteger(), nullable=False),
        sa.Column("booked_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("picked_up_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("delivered_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("returned_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("last_status_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("metadata_json", app.db.types.JSONColumn, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.CheckConstraint(
            "cod_amount_paisa >= 0", name=op.f("ck_consignments_consignment_cod_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_consignments_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_consignments_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consignments")),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "merchant_reference",
            name="uq_consignments_tenant_provider_merchant_reference",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_consignment_id",
            name="uq_consignments_tenant_provider_consignment_id",
        ),
    )
    with op.batch_alter_table("consignments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_consignments_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index("ix_consignments_order", ["order_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_consignments_order_id"), ["order_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_consignments_status"), ["status"], unique=False)
        batch_op.create_index(
            "ix_consignments_tenant_created", ["tenant_id", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_consignments_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            "ix_consignments_tenant_status", ["tenant_id", "provider", "status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_consignments_tracking_code"), ["tracking_code"], unique=False
        )

    op.create_table(
        "order_items",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=True),
        sa.Column("variant_label", sa.String(length=120), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_paisa", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost_snapshot_paisa", sa.BigInteger(), nullable=False),
        sa.Column("discount_paisa", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.CheckConstraint(
            "discount_paisa >= 0", name=op.f("ck_order_items_item_discount_non_negative")
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_items_quantity_positive")),
        sa.CheckConstraint(
            "unit_cost_snapshot_paisa >= 0", name=op.f("ck_order_items_unit_cost_non_negative")
        ),
        sa.CheckConstraint(
            "unit_price_paisa >= 0", name=op.f("ck_order_items_unit_price_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_items_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_order_items_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_order_items_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    with op.batch_alter_table("order_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_order_items_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_order_items_order_id"), ["order_id"], unique=False)
        batch_op.create_index(
            "ix_order_items_order_position", ["order_id", "position"], unique=False
        )
        batch_op.create_index("ix_order_items_product", ["product_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_order_items_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "consignment_items",
        sa.Column("consignment_id", sa.Uuid(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=False),
        sa.Column("qty_shipped", sa.Integer(), nullable=False),
        sa.Column("qty_delivered", sa.Integer(), nullable=False),
        sa.Column("qty_returned", sa.Integer(), nullable=False),
        sa.Column("unit_collectible_paisa", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost_snapshot_paisa", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.CheckConstraint(
            "qty_delivered + qty_returned <= qty_shipped",
            name=op.f("ck_consignment_items_fulfilled_within_shipped"),
        ),
        sa.CheckConstraint(
            "qty_delivered >= 0", name=op.f("ck_consignment_items_qty_delivered_non_negative")
        ),
        sa.CheckConstraint(
            "qty_returned >= 0", name=op.f("ck_consignment_items_qty_returned_non_negative")
        ),
        sa.CheckConstraint(
            "qty_shipped > 0", name=op.f("ck_consignment_items_qty_shipped_positive")
        ),
        sa.CheckConstraint(
            "unit_collectible_paisa >= 0",
            name=op.f("ck_consignment_items_unit_collectible_non_negative"),
        ),
        sa.CheckConstraint(
            "unit_cost_snapshot_paisa >= 0",
            name=op.f("ck_consignment_items_unit_cost_snapshot_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["consignment_id"],
            ["consignments.id"],
            name=op.f("fk_consignment_items_consignment_id_consignments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name=op.f("fk_consignment_items_order_item_id_order_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_consignment_items_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consignment_items")),
        sa.UniqueConstraint(
            "consignment_id",
            "order_item_id",
            name="uq_consignment_items_consignment_id_order_item_id",
        ),
    )
    with op.batch_alter_table("consignment_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_consignment_items_consignment_id"), ["consignment_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_consignment_items_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index("ix_consignment_items_order_item", ["order_item_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_consignment_items_tenant_id"), ["tenant_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("consignment_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_consignment_items_tenant_id"))
        batch_op.drop_index("ix_consignment_items_order_item")
        batch_op.drop_index(batch_op.f("ix_consignment_items_created_at"))
        batch_op.drop_index(batch_op.f("ix_consignment_items_consignment_id"))

    op.drop_table("consignment_items")
    with op.batch_alter_table("order_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_order_items_tenant_id"))
        batch_op.drop_index("ix_order_items_product")
        batch_op.drop_index("ix_order_items_order_position")
        batch_op.drop_index(batch_op.f("ix_order_items_order_id"))
        batch_op.drop_index(batch_op.f("ix_order_items_created_at"))

    op.drop_table("order_items")
    with op.batch_alter_table("consignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_consignments_tracking_code"))
        batch_op.drop_index("ix_consignments_tenant_status")
        batch_op.drop_index(batch_op.f("ix_consignments_tenant_id"))
        batch_op.drop_index("ix_consignments_tenant_created")
        batch_op.drop_index(batch_op.f("ix_consignments_status"))
        batch_op.drop_index(batch_op.f("ix_consignments_order_id"))
        batch_op.drop_index("ix_consignments_order")
        batch_op.drop_index(batch_op.f("ix_consignments_created_at"))

    op.drop_table("consignments")
    with op.batch_alter_table("stock_movements", schema=None) as batch_op:
        batch_op.drop_index("ix_stock_movements_tenant_occurred")
        batch_op.drop_index(batch_op.f("ix_stock_movements_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_stock_movements_reason"))
        batch_op.drop_index("ix_stock_movements_product_occurred")
        batch_op.drop_index(batch_op.f("ix_stock_movements_product_id"))
        batch_op.drop_index("ix_stock_movements_order_id")
        batch_op.drop_index(batch_op.f("ix_stock_movements_created_at"))

    op.drop_table("stock_movements")
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_index("ix_orders_tenant_updated_at")
        batch_op.drop_index("ix_orders_tenant_status_created")
        batch_op.drop_index(batch_op.f("ix_orders_tenant_id"))
        batch_op.drop_index("ix_orders_tenant_created_at")
        batch_op.drop_index("ix_orders_tenant_business_date")
        batch_op.drop_index(batch_op.f("ix_orders_status"))
        batch_op.drop_index("ix_orders_dup_scan")
        batch_op.drop_index(batch_op.f("ix_orders_customer_id"))
        batch_op.drop_index("ix_orders_customer_created")
        batch_op.drop_index(batch_op.f("ix_orders_created_at"))

    op.drop_table("orders")
    with op.batch_alter_table("import_rows", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_import_rows_tenant_id"))
        batch_op.drop_index("ix_import_rows_import_status")
        batch_op.drop_index(batch_op.f("ix_import_rows_import_id"))
        batch_op.drop_index("ix_import_rows_fingerprint")

    op.drop_table("import_rows")
    with op.batch_alter_table("customer_addresses", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_customer_addresses_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_customer_addresses_customer_id"))
        batch_op.drop_index("ix_customer_addresses_customer")
        batch_op.drop_index(batch_op.f("ix_customer_addresses_created_at"))

    op.drop_table("customer_addresses")
    with op.batch_alter_table("sync_mutations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sync_mutations_tenant_id"))
        batch_op.drop_index("ix_sync_mutations_tenant_created")
        batch_op.drop_index("ix_sync_mutations_entity")
        batch_op.drop_index("ix_sync_mutations_device")
        batch_op.drop_index(batch_op.f("ix_sync_mutations_created_at"))

    op.drop_table("sync_mutations")
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_products_tenant_id"))
        batch_op.drop_index("ix_products_tenant_created_at")
        batch_op.drop_index("ix_products_tenant_active_name")
        batch_op.drop_index(batch_op.f("ix_products_created_at"))

    op.drop_table("products")
    with op.batch_alter_table("imports", schema=None) as batch_op:
        batch_op.drop_index("ix_imports_tenant_status")
        batch_op.drop_index("ix_imports_tenant_sha")
        batch_op.drop_index(batch_op.f("ix_imports_tenant_id"))
        batch_op.drop_index("ix_imports_tenant_created")
        batch_op.drop_index(batch_op.f("ix_imports_status"))
        batch_op.drop_index(batch_op.f("ix_imports_created_at"))

    op.drop_table("imports")
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.drop_index("ix_customers_tenant_phone")
        batch_op.drop_index("ix_customers_tenant_last_order")
        batch_op.drop_index(batch_op.f("ix_customers_tenant_id"))
        batch_op.drop_index("ix_customers_tenant_created_at")
        batch_op.drop_index(batch_op.f("ix_customers_created_at"))

    op.drop_table("customers")
