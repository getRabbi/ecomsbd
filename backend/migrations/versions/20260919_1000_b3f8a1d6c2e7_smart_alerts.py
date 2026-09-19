"""V2.2 smart alerts: categories, audiences, alert identity and resolution.

Revision ID: b3f8a1d6c2e7
Revises: e5b7c2d9a413

**``notifications``.** Four columns, all additive:

* ``category`` — what a member switches on and off (MONEY, COURIER, …);
* ``audience`` — the permission a member needs to receive it, decided when it
  was raised; empty keeps the pre-V2.2 "every member" behaviour;
* ``subject_key`` — the alert's identity without a time window, which is what
  deduplication and cooldowns are keyed on;
* ``resolved_at`` — set when the condition clears. Rows are never deleted.

Existing rows are backfilled from their kind so the category filter and role
targeting apply to history too.

**``notification_preferences.muted_categories``.** A member's category
switches. Member rows already had a ``user_id`` column; it is now used.
"""

from alembic import op
import sqlalchemy as sa

import app.db.types

revision = "b3f8a1d6c2e7"
down_revision = "e5b7c2d9a413"
branch_labels = None
depends_on = None

_LEGACY = {
    # kind: (category, audience permission)
    "DELIVERED_BUT_UNPAID": ("MONEY", "money.view"),
    "UNDERPAID": ("RECONCILIATION", "money.view"),
    "STALE_IN_TRANSIT": ("COURIER", "order.book"),
    "RETURNED_NOT_RESTOCKED": ("INVENTORY", "inventory.adjust"),
    "RETURN_SPIKE": ("RETURNS", "customer.risk_view"),
    "WEEKLY_SUMMARY": ("SUMMARY", "money.view"),
    "BUNDLE": ("SUMMARY", ""),
}


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(
            sa.Column("category", sa.String(length=20), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("audience", sa.String(length=40), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("subject_key", sa.String(length=120), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("resolved_at", app.db.types.TZDateTime(), nullable=True))
    op.create_index(
        "ix_notifications_tenant_kind_subject",
        "notifications",
        ["tenant_id", "kind", "subject_key"],
    )

    notifications = sa.table(
        "notifications",
        sa.column("kind", sa.String),
        sa.column("category", sa.String),
        sa.column("audience", sa.String),
    )
    for kind, (category, audience) in _LEGACY.items():
        op.execute(
            notifications.update()
            .where(notifications.c.kind == kind)
            .values(category=category, audience=audience)
        )

    with op.batch_alter_table("notification_preferences") as batch:
        batch.add_column(
            sa.Column(
                "muted_categories",
                app.db.types.JSONColumn,
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_preferences") as batch:
        batch.drop_column("muted_categories")
    op.drop_index("ix_notifications_tenant_kind_subject", table_name="notifications")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_column("resolved_at")
        batch.drop_column("subject_key")
        batch.drop_column("audience")
        batch.drop_column("category")
