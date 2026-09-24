"""External risk providers, their lookups, and anonymous network cohort cells."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, JSONColumn, TZDateTime

revision = "a37001"
down_revision = "a36001"
branch_labels = None
depends_on = None

TABLES = ("risk_provider_connections", "external_risk_lookups", "network_benchmark_cells")


def _tenant_columns():
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
        "risk_provider_connections",
        *_tenant_columns(),
        sa.Column("provider_id", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("credentials_enc", sa.Text(), nullable=True),
        sa.Column("credential_hint", sa.String(8), nullable=True),
        sa.Column("config", JSONColumn, nullable=False),
        sa.Column("cache_ttl_hours", sa.Integer(), nullable=False),
        sa.Column("health", sa.String(16), nullable=False),
        sa.Column("last_tested_at", TZDateTime(), nullable=True),
        sa.Column("last_test_result", sa.String(32), nullable=True),
        sa.Column("last_success_at", TZDateTime(), nullable=True),
        sa.Column("last_failure_at", TZDateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(40), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("rate_limited_until", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "provider_id"),
    )
    op.create_index(
        "ix_risk_provider_connections_tenant_id", "risk_provider_connections", ["tenant_id"]
    )
    op.create_index(
        "ix_risk_provider_connections_created_at", "risk_provider_connections", ["created_at"]
    )

    op.create_table(
        "external_risk_lookups",
        *_tenant_columns(),
        sa.Column(
            "customer_id", GUID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("facts", JSONColumn, nullable=False),
        sa.Column("dropped_facts", sa.Integer(), nullable=False),
        sa.Column("provider_observed_at", TZDateTime(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.String(24), nullable=True),
        sa.Column("provider_reference", sa.String(120), nullable=True),
        sa.Column("checked_at", TZDateTime(), nullable=False),
        sa.Column("expires_at", TZDateTime(), nullable=True),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("requested_by", GUID, nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
    )
    op.create_index("ix_external_risk_lookups_tenant_id", "external_risk_lookups", ["tenant_id"])
    op.create_index("ix_external_risk_lookups_created_at", "external_risk_lookups", ["created_at"])
    op.create_index(
        "ix_external_risk_lookups_customer",
        "external_risk_lookups",
        ["tenant_id", "customer_id", "provider_id"],
    )

    op.create_table(
        "network_benchmark_cells",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("policy_version", sa.String(24), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("dimension", sa.String(16), nullable=False),
        sa.Column("cohort", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("value", sa.Integer(), nullable=True),
        sa.Column("precision", sa.Integer(), nullable=True),
        sa.Column("unit", sa.String(12), nullable=False),
        sa.Column("shops_band", sa.String(12), nullable=True),
        sa.Column("sample_band", sa.String(12), nullable=True),
        sa.UniqueConstraint("period", "policy_version", "metric", "dimension", "cohort"),
    )
    op.create_index(
        "ix_network_benchmark_cells_created_at", "network_benchmark_cells", ["created_at"]
    )
    op.create_index("ix_network_benchmark_cells_period", "network_benchmark_cells", ["period"])

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
        for table in TABLES:
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
