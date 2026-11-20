"""V4 phase 4: the shop's Autopilot switch (on, consent, pause, manual-queue mode)
on ``shop_delivery_rules``, and ``autopilot_decisions`` (one per chat draft)."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime

revision = "a44001"
down_revision = "a43001"
branch_labels = None
depends_on = None

_TABLES = ["autopilot_decisions"]


def upgrade():
    with op.batch_alter_table("shop_delivery_rules") as batch:
        batch.add_column(
            sa.Column("autopilot_on", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("autopilot_consent_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("autopilot_consent_by", GUID, nullable=True))
        batch.add_column(sa.Column("autopilot_paused_until", TZDateTime(), nullable=True))
        batch.add_column(
            sa.Column(
                "autopilot_manual_queue", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )

    op.create_table(
        "autopilot_decisions",
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("draft_id", GUID, nullable=False),
        sa.Column("outcome", sa.String(12), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=True),
        sa.Column("order_id", GUID, sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("execution_id", GUID, nullable=True),
        sa.UniqueConstraint("tenant_id", "draft_id", name="uq_autopilot_decisions_draft"),
        sa.CheckConstraint(
            "(outcome = 'CONVERTED' AND order_id IS NOT NULL)"
            " OR (outcome = 'HELD' AND order_id IS NULL AND reason_code IS NOT NULL)",
            name="ck_autopilot_decisions_outcome",
        ),
    )
    op.create_index("ix_autopilot_decisions_tenant_id", "autopilot_decisions", ["tenant_id"])
    op.create_index("ix_autopilot_decisions_created_at", "autopilot_decisions", ["created_at"])
    op.create_index(
        "ix_autopilot_decisions_tenant_created",
        "autopilot_decisions",
        ["tenant_id", "created_at"],
    )

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
    with op.batch_alter_table("shop_delivery_rules") as batch:
        batch.drop_column("autopilot_manual_queue")
        batch.drop_column("autopilot_paused_until")
        batch.drop_column("autopilot_consent_by")
        batch.drop_column("autopilot_consent_at")
        batch.drop_column("autopilot_on")
