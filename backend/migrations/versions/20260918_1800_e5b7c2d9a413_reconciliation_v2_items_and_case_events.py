"""Reconciliation V2: expected-vs-actual items, case events, accepted charges.

Revision ID: e5b7c2d9a413
Revises: a4d92f1c60b8

**``reconciliation_items``.** One row per parcel a statement touched (or per
statement row nobody could place): what the courier should have paid, what it
did pay, and the difference. Derived from the receivable, the payout lines and
adjustments and the charges on record, and recomputed on every reconcile — it
explains the ledger and is never read back as money. Unique on
(tenant, subject_key) so a re-run updates rather than accumulates.

**``reconciliation_case_events``.** Append-only history of a case: notes,
status changes, reopenings, the engine closing one because the money arrived.
The case row keeps only its current state.

**``payout_adjustments.accepted_at`` / ``accepted_by``.** Set once when a
person accepts a courier deduction into the ledger. Null to value, never back,
which is what makes accepting the same charges twice a no-op.

Nothing existing is rewritten: two new tables and two nullable columns.
"""

from alembic import op
import sqlalchemy as sa

import app.db.types

revision = "e5b7c2d9a413"
down_revision = "a4d92f1c60b8"
branch_labels = None
depends_on = None

_NEW_TABLES = ("reconciliation_items", "reconciliation_case_events")


def upgrade() -> None:
    op.create_table(
        "reconciliation_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("payout_id", sa.Uuid(), nullable=True),
        sa.Column("payout_line_id", sa.Uuid(), nullable=True),
        sa.Column("receivable_id", sa.Uuid(), nullable=True),
        sa.Column("consignment_id", sa.Uuid(), nullable=True),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("merchant_reference", sa.String(length=120), nullable=True),
        sa.Column("tracking_code", sa.String(length=120), nullable=True),
        sa.Column("settlement_date", sa.Date(), nullable=True),
        sa.Column("expected_cod_paisa", sa.BigInteger(), nullable=True),
        sa.Column("actual_cod_paisa", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("expected_charge_paisa", sa.BigInteger(), nullable=True),
        sa.Column("expected_charge_source", sa.String(length=16), nullable=True),
        sa.Column("actual_charge_paisa", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("expected_net_paisa", sa.BigInteger(), nullable=True),
        sa.Column("actual_net_paisa", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("difference_paisa", sa.BigInteger(), nullable=True),
        sa.Column("charges_pending_paisa", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", app.db.types.JSONColumn, nullable=False),
        sa.Column("evaluated_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_reconciliation_items_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_items")),
        sa.UniqueConstraint("tenant_id", "subject_key", name="uq_reconciliation_items_subject"),
    )
    with op.batch_alter_table("reconciliation_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_reconciliation_items_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_reconciliation_items_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_reconciliation_items_receivable_id"), ["receivable_id"], unique=False
        )
        batch_op.create_index(
            "ix_reconciliation_items_tenant_status", ["tenant_id", "status"], unique=False
        )
        batch_op.create_index(
            "ix_reconciliation_items_tenant_provider_date",
            ["tenant_id", "provider", "settlement_date"],
            unique=False,
        )
        batch_op.create_index(
            "ix_reconciliation_items_tenant_payout", ["tenant_id", "payout_id"], unique=False
        )

    op.create_table(
        "reconciliation_case_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_reconciliation_case_events_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["reconciliation_cases.id"],
            name=op.f("fk_reconciliation_case_events_case_id_reconciliation_cases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_case_events")),
    )
    with op.batch_alter_table("reconciliation_case_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_reconciliation_case_events_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            "ix_reconciliation_case_events_case", ["case_id", "created_at"], unique=False
        )

    with op.batch_alter_table("payout_adjustments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("accepted_at", app.db.types.TZDateTime(), nullable=True))
        batch_op.add_column(sa.Column("accepted_by", sa.Uuid(), nullable=True))

    # Same boundary the Supabase migration drew for every business table:
    # mobile clients never read these directly.
    db = op.get_bind()
    if db.dialect.name == "postgresql":
        quote = db.dialect.identifier_preparer.quote
        for table in _NEW_TABLES:
            op.execute(f"ALTER TABLE public.{quote(table)} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    with op.batch_alter_table("payout_adjustments", schema=None) as batch_op:
        batch_op.drop_column("accepted_by")
        batch_op.drop_column("accepted_at")

    with op.batch_alter_table("reconciliation_case_events", schema=None) as batch_op:
        batch_op.drop_index("ix_reconciliation_case_events_case")
        batch_op.drop_index(batch_op.f("ix_reconciliation_case_events_tenant_id"))
    op.drop_table("reconciliation_case_events")

    with op.batch_alter_table("reconciliation_items", schema=None) as batch_op:
        batch_op.drop_index("ix_reconciliation_items_tenant_payout")
        batch_op.drop_index("ix_reconciliation_items_tenant_provider_date")
        batch_op.drop_index("ix_reconciliation_items_tenant_status")
        batch_op.drop_index(batch_op.f("ix_reconciliation_items_receivable_id"))
        batch_op.drop_index(batch_op.f("ix_reconciliation_items_created_at"))
        batch_op.drop_index(batch_op.f("ix_reconciliation_items_tenant_id"))
    op.drop_table("reconciliation_items")
