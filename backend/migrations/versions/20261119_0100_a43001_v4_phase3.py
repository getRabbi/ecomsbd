"""V4 phase 3: the platform's voice provider accounts (WP3.1) and call callbacks (WP3.2)."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, JSONColumn, TZDateTime

revision = "a43001"
down_revision = "a42001"
branch_labels = None
depends_on = None

# Platform-level (not tenant-owned): RLS on, client roles revoked.
_TABLES = ["voice_provider_connections", "voice_callback_deliveries", "voice_call_events"]


def upgrade():
    # Platform-level (not tenant-owned): one row per provider.
    op.create_table(
        "voice_provider_connections",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("callback_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("key_version", sa.String(20), nullable=True),
        sa.Column("masked_identifier", sa.String(40), nullable=True),
        sa.Column("caller_id", sa.String(20), nullable=True),
        sa.Column("config", JSONColumn, nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("updated_by", sa.String(120), nullable=False),
        sa.Column("credentials_rotated_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("provider", name="uq_voice_provider_connections_provider"),
    )
    op.create_index(
        "ix_voice_provider_connections_created_at", "voice_provider_connections", ["created_at"]
    )

    # WP3.2: every inbound callback, stored before it is verified.
    op.create_table(
        "voice_callback_deliveries",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("header_names", JSONColumn, nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("delivery_count", sa.Integer(), nullable=False),
        sa.Column("received_at", TZDateTime(), nullable=False),
        sa.Column("processed_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("provider", "body_sha256", name="uq_voice_callback_deliveries_body"),
        sa.CheckConstraint("delivery_count > 0", name="delivery_count_positive"),
    )
    op.create_index(
        "ix_voice_callback_deliveries_state",
        "voice_callback_deliveries",
        ["provider", "state", "received_at"],
    )

    # WP3.2: one row per provider call event, deduplicated.
    op.create_table(
        "voice_call_events",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_ref", sa.String(120), nullable=False),
        sa.Column("event_key", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("digit", sa.String(1), nullable=True),
        sa.Column("client_ref", sa.String(80), nullable=True),
        sa.Column("occurred_at", TZDateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("tenant_id", GUID, nullable=True),
        sa.Column("attempt_id", GUID, nullable=True),
        sa.Column("delivery_id", GUID, nullable=True),
        sa.UniqueConstraint(
            "provider", "provider_ref", "event_key", name="uq_voice_call_events_event"
        ),
    )
    op.create_index("ix_voice_call_events_created_at", "voice_call_events", ["created_at"])
    op.create_index("ix_voice_call_events_attempt", "voice_call_events", ["attempt_id"])

    # WP3.2: a callback names the provider's call.
    op.create_index(
        "ix_confirmation_attempts_provider_ref",
        "confirmation_attempts",
        ["provider", "provider_ref"],
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
    op.drop_index("ix_confirmation_attempts_provider_ref", table_name="confirmation_attempts")
    for table in reversed(_TABLES):
        op.drop_table(table)
