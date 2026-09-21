"""Scoped public credentials, write receipts and outbound webhooks."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime, JSONColumn

revision = "a23003"
down_revision = "a23002"
branch_labels = None
depends_on = None


def _base():
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
        "public_api_keys",
        *_base(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("scopes", JSONColumn, nullable=False),
        sa.Column("rate_limit", sa.Integer(), nullable=False),
        sa.Column("created_by", GUID, nullable=False),
        sa.Column("revoked_at", TZDateTime(), nullable=True),
    )
    op.create_index("ix_public_api_keys_tenant_id", "public_api_keys", ["tenant_id"])
    op.create_index("ix_public_api_keys_created_at", "public_api_keys", ["created_at"])
    op.create_table(
        "public_api_write_receipts",
        *_base(),
        sa.Column("key_id", GUID, sa.ForeignKey("public_api_keys.id"), nullable=False),
        sa.Column("endpoint", sa.String(160), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", JSONColumn, nullable=False),
        sa.UniqueConstraint("tenant_id", "key_id", "endpoint", "idempotency_key"),
    )
    op.create_index(
        "ix_public_api_write_receipts_tenant_id", "public_api_write_receipts", ["tenant_id"]
    )
    op.create_index(
        "ix_public_api_write_receipts_created_at", "public_api_write_receipts", ["created_at"]
    )
    op.create_table(
        "outbound_webhook_endpoints",
        *_base(),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("topics", JSONColumn, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_outbound_webhook_endpoints_tenant_id", "outbound_webhook_endpoints", ["tenant_id"]
    )
    op.create_index(
        "ix_outbound_webhook_endpoints_created_at", "outbound_webhook_endpoints", ["created_at"]
    )
    op.create_table(
        "outbound_webhook_deliveries",
        *_base(),
        sa.Column(
            "endpoint_id", GUID, sa.ForeignKey("outbound_webhook_endpoints.id"), nullable=False
        ),
        sa.Column("event_id", GUID, nullable=False),
        sa.Column("payload", JSONColumn, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TZDateTime(), nullable=False),
        sa.Column("last_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(80), nullable=True),
        sa.UniqueConstraint("tenant_id", "endpoint_id", "event_id"),
    )
    op.create_index(
        "ix_outbound_webhook_deliveries_tenant_id", "outbound_webhook_deliveries", ["tenant_id"]
    )
    op.create_index(
        "ix_outbound_webhook_deliveries_created_at", "outbound_webhook_deliveries", ["created_at"]
    )
    op.create_table(
        "outbound_webhook_attempts",
        *_base(),
        sa.Column(
            "delivery_id", GUID, sa.ForeignKey("outbound_webhook_deliveries.id"), nullable=False
        ),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(80), nullable=True),
    )
    op.create_index(
        "ix_outbound_webhook_attempts_tenant_id", "outbound_webhook_attempts", ["tenant_id"]
    )
    op.create_index(
        "ix_outbound_webhook_attempts_created_at", "outbound_webhook_attempts", ["created_at"]
    )
    op.create_index("ix_webhook_due", "outbound_webhook_deliveries", ["status", "next_attempt_at"])
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
        for table in [
            "public_api_keys",
            "public_api_write_receipts",
            "outbound_webhook_endpoints",
            "outbound_webhook_deliveries",
            "outbound_webhook_attempts",
        ]:
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("outbound_webhook_attempts")
    op.drop_table("outbound_webhook_deliveries")
    op.drop_table("outbound_webhook_endpoints")
    op.drop_table("public_api_write_receipts")
    op.drop_table("public_api_keys")
