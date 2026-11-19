"""V4 phase 3: the platform's voice provider accounts (WP3.1), call callbacks (WP3.2) and
each shop's intro recordings (WP3.3)."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, JSONColumn, TZDateTime

revision = "a43001"
down_revision = "a42001"
branch_labels = None
depends_on = None

# Platform-level and tenant-owned alike: RLS on, client roles revoked.
_TABLES = [
    "voice_provider_connections",
    "voice_callback_deliveries",
    "voice_call_events",
    "voice_intro_audio",
]

# WP3.3: one recording in use per shop. Partial unique index on PostgreSQL and SQLite.
_INTRO_ON_PREDICATE = "state = 'ON'"


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

    # WP3.3: tenant-owned versions of a shop's confirmation-call intro.
    op.create_table(
        "voice_intro_audio",
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("shop_name", sa.String(255), nullable=False),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("previewed_at", TZDateTime(), nullable=True),
        sa.Column("previewed_by", GUID, nullable=True),
        sa.Column("approved_at", TZDateTime(), nullable=True),
        sa.Column("approved_by", GUID, nullable=True),
        sa.Column("retired_at", TZDateTime(), nullable=True),
        sa.Column("retired_reason", sa.String(24), nullable=True),
        sa.Column("provider_refs", JSONColumn, nullable=False),
        sa.UniqueConstraint("tenant_id", "version", name="uq_voice_intro_audio_version"),
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.CheckConstraint("byte_size > 0", name="byte_size_positive"),
    )
    op.create_index("ix_voice_intro_audio_tenant_id", "voice_intro_audio", ["tenant_id"])
    op.create_index("ix_voice_intro_audio_created_at", "voice_intro_audio", ["created_at"])
    op.create_index(
        "uq_voice_intro_audio_on",
        "voice_intro_audio",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text(_INTRO_ON_PREDICATE),
        sqlite_where=sa.text(_INTRO_ON_PREDICATE),
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
