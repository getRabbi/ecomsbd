"""V4 phase 8 (WP7.1): ``sms_provider_connections``, the platform's SMS gateway
accounts (Alpha SMS). Platform-level, one row per provider."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime

revision = "a48001"
down_revision = "a47001"
branch_labels = None
depends_on = None

# Platform-level: RLS on, client roles revoked.
_TABLES = ["sms_provider_connections"]


def upgrade():
    op.create_table(
        "sms_provider_connections",
        sa.Column("id", GUID, primary_key=True),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("key_version", sa.String(20), nullable=True),
        sa.Column("masked_identifier", sa.String(40), nullable=True),
        sa.Column("sender_id", sa.String(20), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("updated_by", sa.String(120), nullable=False),
        sa.Column("credentials_rotated_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("provider", name="uq_sms_provider_connections_provider"),
    )
    op.create_index(
        "ix_sms_provider_connections_created_at", "sms_provider_connections", ["created_at"]
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
