"""V4 phase 0: product_events (funnel), ai_usage (model-call cost), shop_delivery_rules.

Also widens chat_order_drafts.cod_source to 16 characters for CHAT_MISMATCH.
"""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, JSONColumn, TZDateTime

revision = "a40001"
down_revision = "a39001"
branch_labels = None
depends_on = None

_TABLES = ["product_events", "ai_usage", "shop_delivery_rules"]


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

    op.create_table(
        "ai_usage",
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
    )
    op.create_index("ix_ai_usage_tenant_id", "ai_usage", ["tenant_id"])
    op.create_index("ix_ai_usage_created_at", "ai_usage", ["created_at"])
    op.create_index(
        "ix_ai_usage_tenant_purpose_created", "ai_usage", ["tenant_id", "purpose", "created_at"]
    )

    op.create_table(
        "shop_delivery_rules",
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("inside_dhaka_charge_paisa", sa.BigInteger(), nullable=False),
        sa.Column("dhaka_suburb_charge_paisa", sa.BigInteger(), nullable=False),
        sa.Column("outside_dhaka_charge_paisa", sa.BigInteger(), nullable=False),
        sa.Column("default_courier", sa.String(32), nullable=True),
        sa.Column("max_auto_cod_paisa", sa.BigInteger(), nullable=False),
        sa.Column("risk_low_bps", sa.Integer(), nullable=False),
        sa.Column("trusted_network_bps", sa.Integer(), nullable=False),
        sa.Column("trusted_repeat_min", sa.Integer(), nullable=False),
        sa.Column("quiet_start", sa.String(5), nullable=False),
        sa.Column("quiet_end", sa.String(5), nullable=False),
        sa.Column("autopilot_daily_cap", sa.Integer(), nullable=False),
        sa.Column("call_everyone", sa.Boolean(), nullable=False),
        sa.Column("call_nobody", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_shop_delivery_rules_tenant"),
        sa.CheckConstraint(
            "inside_dhaka_charge_paisa >= 0 AND dhaka_suburb_charge_paisa >= 0"
            " AND outside_dhaka_charge_paisa >= 0",
            name="ck_shop_delivery_rules_charges",
        ),
        sa.CheckConstraint("max_auto_cod_paisa >= 0", name="ck_shop_delivery_rules_max_cod"),
        sa.CheckConstraint(
            "risk_low_bps BETWEEN 0 AND 10000 AND trusted_network_bps BETWEEN 0 AND 10000",
            name="ck_shop_delivery_rules_bps",
        ),
        sa.CheckConstraint(
            "trusted_repeat_min >= 0 AND autopilot_daily_cap >= 0",
            name="ck_shop_delivery_rules_counts",
        ),
        sa.CheckConstraint(
            "NOT (call_everyone AND call_nobody)", name="ck_shop_delivery_rules_call_switches"
        ),
    )
    op.create_index("ix_shop_delivery_rules_tenant_id", "shop_delivery_rules", ["tenant_id"])
    op.create_index("ix_shop_delivery_rules_created_at", "shop_delivery_rules", ["created_at"])

    with op.batch_alter_table("chat_order_drafts") as batch_op:
        batch_op.alter_column(
            "cod_source", existing_type=sa.String(12), type_=sa.String(16), existing_nullable=True
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
    with op.batch_alter_table("chat_order_drafts") as batch_op:
        batch_op.alter_column(
            "cod_source", existing_type=sa.String(16), type_=sa.String(12), existing_nullable=True
        )
    for table in reversed(_TABLES):
        op.drop_table(table)
