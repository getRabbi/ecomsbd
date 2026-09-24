"""Integrations Hub: connections, provider events, sync runs, API key last use."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime, JSONColumn

revision = "a31001"
down_revision = "a23007"
branch_labels = None
depends_on = None

_TABLES = ["integration_connections", "integration_events", "integration_sync_runs"]


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


def upgrade():
    op.create_table(
        "integration_connections",
        *_base(),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("account_id", sa.String(255), nullable=True),
        sa.Column("account_name", sa.String(200), nullable=True),
        sa.Column("account_key", sa.String(255), nullable=True),
        sa.Column("source_id", GUID, sa.ForeignKey("external_order_sources.id"), nullable=True),
        sa.Column("credentials_enc", sa.Text(), nullable=True),
        sa.Column("webhook_token", sa.String(64), nullable=True),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
        sa.Column("state_hash", sa.String(64), nullable=True),
        sa.Column("state_expires_at", TZDateTime(), nullable=True),
        sa.Column("config", JSONColumn, nullable=False),
        sa.Column("webhook_state", sa.String(24), nullable=True),
        sa.Column("sync_state", sa.String(24), nullable=True),
        sa.Column("last_success_at", TZDateTime(), nullable=True),
        sa.Column("last_webhook_at", TZDateTime(), nullable=True),
        sa.Column("last_sync_at", TZDateTime(), nullable=True),
        sa.Column("last_error_at", TZDateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_by", GUID, nullable=False),
        sa.UniqueConstraint("provider", "account_key"),
        sa.UniqueConstraint("webhook_token"),
        sa.UniqueConstraint("state_hash"),
    )
    _indexes("integration_connections")
    op.create_table(
        "integration_events",
        *_base(),
        sa.Column(
            "connection_id", GUID, sa.ForeignKey("integration_connections.id"), nullable=False
        ),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("delivery_id", sa.String(200), nullable=True),
        sa.Column("external_ref", sa.String(200), nullable=True),
        sa.Column("hint", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TZDateTime(), nullable=True),
        sa.Column("order_id", GUID, sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("resolved_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "connection_id", "delivery_id"),
    )
    _indexes("integration_events")
    op.create_index("ix_integration_event_due", "integration_events", ["status", "next_attempt_at"])
    op.create_index(
        "ix_integration_event_connection", "integration_events", ["connection_id", "created_at"]
    )
    op.create_table(
        "integration_sync_runs",
        *_base(),
        sa.Column(
            "connection_id", GUID, sa.ForeignKey("integration_connections.id"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("since", TZDateTime(), nullable=False),
        sa.Column("until", TZDateTime(), nullable=False),
        sa.Column("cursor", sa.String(500), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("imported", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", TZDateTime(), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("finished_at", TZDateTime(), nullable=True),
        sa.Column("created_by", GUID, nullable=True),
    )
    _indexes("integration_sync_runs")
    op.create_index("ix_integration_sync_due", "integration_sync_runs", ["status", "next_run_at"])
    op.create_index(
        "ix_integration_sync_connection", "integration_sync_runs", ["connection_id", "created_at"]
    )
    with op.batch_alter_table("public_api_keys") as batch:
        batch.add_column(sa.Column("last_used_at", TZDateTime(), nullable=True))
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
    with op.batch_alter_table("public_api_keys") as batch:
        batch.drop_column("last_used_at")
    for table in reversed(_TABLES):
        op.drop_table(table)
