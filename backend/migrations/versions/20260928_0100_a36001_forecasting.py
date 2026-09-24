"""Forecasting: daily demand-forecast snapshots and supplier lead times."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime

revision = "a36001"
down_revision = "a35001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "demand_forecasts",
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "product_id", GUID, sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "variant_id",
            GUID,
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("item_key", sa.String(80), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("rate_milli", sa.Integer(), nullable=True),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("predicted_units", sa.Integer(), nullable=True),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("incoming", sa.Integer(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("reorder_point", sa.Integer(), nullable=True),
        sa.Column("suggested_quantity", sa.Integer(), nullable=True),
        sa.Column("stockout_on", sa.Date(), nullable=True),
        sa.Column("at_risk", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "as_of", "item_key"),
    )
    op.create_index("ix_demand_forecasts_tenant_id", "demand_forecasts", ["tenant_id"])
    op.create_index("ix_demand_forecasts_created_at", "demand_forecasts", ["created_at"])
    op.create_index("ix_demand_forecasts_as_of", "demand_forecasts", ["tenant_id", "as_of"])

    with op.batch_alter_table("suppliers") as batch:
        batch.add_column(sa.Column("lead_time_days", sa.Integer(), nullable=True))

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
        op.execute(f"REVOKE ALL ON TABLE public.demand_forecasts FROM {grantees}")
        op.execute("ALTER TABLE public.demand_forecasts ENABLE ROW LEVEL SECURITY")


def downgrade():
    with op.batch_alter_table("suppliers") as batch:
        batch.drop_column("lead_time_days")
    op.drop_table("demand_forecasts")
