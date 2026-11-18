"""V4 phase 1: device ingest keys and pairing codes (WP1.1)."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime

revision = "a41001"
down_revision = "a40001"
branch_labels = None
depends_on = None

_TABLES = ["device_ingest_keys", "device_pairing_codes"]


def _base_columns():
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
        "device_ingest_keys",
        *_base_columns(),
        sa.Column(
            "connection_id",
            GUID,
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_last4", sa.String(4), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("created_by", GUID, nullable=False),
        sa.Column("revoked_at", TZDateTime(), nullable=True),
        sa.Column("last_used_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_device_ingest_keys_hash"),
    )
    op.create_index("ix_device_ingest_keys_tenant_id", "device_ingest_keys", ["tenant_id"])
    op.create_index("ix_device_ingest_keys_created_at", "device_ingest_keys", ["created_at"])
    op.create_index("ix_device_ingest_keys_connection", "device_ingest_keys", ["connection_id"])

    op.create_table(
        "device_pairing_codes",
        *_base_columns(),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_by", GUID, nullable=False),
        sa.Column("expires_at", TZDateTime(), nullable=False),
        sa.Column("claimed_at", TZDateTime(), nullable=True),
        sa.Column("connection_id", GUID, nullable=True),
    )
    op.create_index("ix_device_pairing_codes_tenant_id", "device_pairing_codes", ["tenant_id"])
    op.create_index("ix_device_pairing_codes_created_at", "device_pairing_codes", ["created_at"])
    op.create_index("ix_device_pairing_codes_hash", "device_pairing_codes", ["code_hash"])

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
