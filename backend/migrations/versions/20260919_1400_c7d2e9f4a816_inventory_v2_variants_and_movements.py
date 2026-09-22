"""V2.2 inventory: optional variants, a traceable ledger and return receipt.

Revision ID: c7d2e9f4a816
Revises: b3f8a1d6c2e7

**``product_variants``.** New. A product gains variants only when the seller
adds one; every existing product stays a simple product (``has_variants`` is
false) and keeps its id, stock and threshold.

**``stock_movements``.** Additive: ``variant_id``, ``idempotency_key`` (unique
per shop, so a retried request cannot move stock twice), and a restock's
``reference`` and ``unit_cost_paisa``.

**``order_items.variant_id``** says which variant a line deducts.

**``consignment_items``** gains the seller's return decision:
``qty_restocked``, ``qty_not_restocked`` and ``return_received_at``. From here
on a courier saying RETURNED no longer restocks anything by itself.

Backfills, both idempotent:

* A product whose ``stock_on_hand`` differs from the sum of its movements (a
  row written before the ledger was the only writer) gets one ``OPENING``
  movement for the difference, keyed ``inventory-v2-opening:<product>``. The
  stock figure itself is not touched, so nothing is counted twice, and a second
  run finds the ledger already agreeing and inserts nothing.
* A returned item that pre-V2.2 code already restored automatically is marked
  received and restocked, so it is not asked about again.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.types

revision: str = "c7d2e9f4a816"
down_revision: str | None = "b3f8a1d6c2e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESTORE_REASONS = ("RETURN_RESTORE", "PARTIAL_RETURN_RESTORE", "CANCEL_RESTORE")


def upgrade() -> None:
    op.create_table(
        "product_variants",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("options", app.db.types.JSONColumn, nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=True),
        sa.Column("price_paisa", sa.BigInteger(), nullable=True),
        sa.Column("cost_paisa", sa.BigInteger(), nullable=True),
        sa.Column("stock_on_hand", sa.Integer(), nullable=False),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.CheckConstraint(
            "cost_paisa IS NULL OR cost_paisa >= 0",
            name=op.f("ck_product_variants_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "price_paisa IS NULL OR price_paisa >= 0",
            name=op.f("ck_product_variants_price_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_variants_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_product_variants_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_variants")),
        sa.UniqueConstraint("product_id", "name", name="uq_product_variants_product_id_name"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_product_variants_tenant_id_sku"),
    )
    with op.batch_alter_table("product_variants", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_product_variants_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_product_variants_product_id"), ["product_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_product_variants_tenant_id"), ["tenant_id"], unique=False
        )

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("has_variants", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("stock_movements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("variant_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("reference", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("unit_cost_paisa", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=120), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_stock_movements_variant_id_product_variants"),
            "product_variants",
            ["variant_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_stock_movements_tenant_id_idempotency_key", ["tenant_id", "idempotency_key"]
        )
        batch_op.create_index(
            "ix_stock_movements_variant_occurred", ["variant_id", "occurred_at"], unique=False
        )

    with op.batch_alter_table("order_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("variant_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_order_items_variant_id_product_variants"),
            "product_variants",
            ["variant_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("consignment_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("qty_restocked", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("qty_not_restocked", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("return_received_at", app.db.types.TZDateTime(), nullable=True)
        )

    _backfill_returns_already_restocked()

    with op.batch_alter_table("consignment_items", schema=None) as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_consignment_items_return_decided_within_returned"),
            "qty_restocked >= 0 AND qty_not_restocked >= 0 "
            "AND qty_restocked + qty_not_restocked <= qty_returned",
        )

    _backfill_opening_balances()


def _backfill_opening_balances() -> None:
    """Make the ledger agree with every product's current stock, once."""
    bind = op.get_bind()
    products = sa.table(
        "products",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("stock_on_hand", sa.Integer()),
        sa.column("stock_tracking_enabled", sa.Boolean()),
    )
    movements = sa.table(
        "stock_movements",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("product_id", sa.Uuid()),
        sa.column("quantity_delta", sa.Integer()),
        sa.column("balance_after", sa.Integer()),
        sa.column("reason", sa.String()),
        sa.column("source", sa.String()),
        sa.column("note", sa.String()),
        sa.column("idempotency_key", sa.String()),
        sa.column("occurred_at", app.db.types.TZDateTime()),
        sa.column("created_at", app.db.types.TZDateTime()),
    )
    ledger = (
        sa.select(
            movements.c.product_id,
            sa.func.sum(movements.c.quantity_delta).label("total"),
        )
        .group_by(movements.c.product_id)
        .subquery()
    )
    rows = bind.execute(
        sa.select(
            products.c.id,
            products.c.tenant_id,
            products.c.stock_on_hand,
            sa.func.coalesce(ledger.c.total, 0),
        )
        .select_from(products.outerjoin(ledger, ledger.c.product_id == products.c.id))
        .where(
            products.c.stock_tracking_enabled.is_(True),
            products.c.stock_on_hand != sa.func.coalesce(ledger.c.total, 0),
        )
    ).all()
    if not rows:
        return

    from app.core.clock import utc_now

    now = utc_now()
    for product_id, tenant_id, stock_on_hand, total in rows:
        key = f"inventory-v2-opening:{product_id}"
        already = bind.execute(
            sa.select(movements.c.id).where(
                movements.c.tenant_id == tenant_id, movements.c.idempotency_key == key
            )
        ).first()
        if already is not None:
            continue
        bind.execute(
            movements.insert().values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                product_id=product_id,
                quantity_delta=int(stock_on_hand) - int(total),
                balance_after=int(stock_on_hand),
                reason="OPENING",
                source="SYSTEM",
                note="Opening balance carried into the stock history",
                idempotency_key=key,
                occurred_at=now,
                created_at=now,
            )
        )


def _backfill_returns_already_restocked() -> None:
    """Returned items that the old automatic restore already put back."""
    bind = op.get_bind()
    items = sa.table(
        "consignment_items",
        sa.column("id", sa.Uuid()),
        sa.column("consignment_id", sa.Uuid()),
        sa.column("order_item_id", sa.Uuid()),
        sa.column("qty_returned", sa.Integer()),
        sa.column("qty_restocked", sa.Integer()),
        sa.column("return_received_at", app.db.types.TZDateTime()),
    )
    movements = sa.table(
        "stock_movements",
        sa.column("consignment_id", sa.Uuid()),
        sa.column("order_item_id", sa.Uuid()),
        sa.column("quantity_delta", sa.Integer()),
        sa.column("reason", sa.String()),
        sa.column("occurred_at", app.db.types.TZDateTime()),
    )
    restored = bind.execute(
        sa.select(
            movements.c.consignment_id,
            movements.c.order_item_id,
            sa.func.sum(movements.c.quantity_delta),
            sa.func.max(movements.c.occurred_at),
        )
        .where(
            movements.c.reason.in_(_RESTORE_REASONS),
            movements.c.consignment_id.is_not(None),
            movements.c.order_item_id.is_not(None),
        )
        .group_by(movements.c.consignment_id, movements.c.order_item_id)
    ).all()
    for consignment_id, order_item_id, quantity, occurred_at in restored:
        row = bind.execute(
            sa.select(items.c.id, items.c.qty_returned).where(
                items.c.consignment_id == consignment_id,
                items.c.order_item_id == order_item_id,
                items.c.return_received_at.is_(None),
            )
        ).first()
        if row is None or not row[1]:
            continue
        bind.execute(
            items.update()
            .where(items.c.id == row[0])
            .values(
                qty_restocked=min(int(row[1]), int(quantity or 0)),
                return_received_at=occurred_at,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("consignment_items", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_consignment_items_return_decided_within_returned"), type_="check"
        )
        batch_op.drop_column("return_received_at")
        batch_op.drop_column("qty_not_restocked")
        batch_op.drop_column("qty_restocked")

    with op.batch_alter_table("order_items", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_order_items_variant_id_product_variants"), type_="foreignkey"
        )
        batch_op.drop_column("variant_id")

    with op.batch_alter_table("stock_movements", schema=None) as batch_op:
        batch_op.drop_index("ix_stock_movements_variant_occurred")
        batch_op.drop_constraint("uq_stock_movements_tenant_id_idempotency_key", type_="unique")
        batch_op.drop_constraint(
            batch_op.f("fk_stock_movements_variant_id_product_variants"), type_="foreignkey"
        )
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("unit_cost_paisa")
        batch_op.drop_column("reference")
        batch_op.drop_column("variant_id")

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_column("has_variants")

    with op.batch_alter_table("product_variants", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_product_variants_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_product_variants_product_id"))
        batch_op.drop_index(batch_op.f("ix_product_variants_created_at"))
    op.drop_table("product_variants")
