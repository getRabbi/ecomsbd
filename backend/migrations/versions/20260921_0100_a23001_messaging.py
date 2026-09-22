"""Transactional messaging; business tables are accessible only through FastAPI."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime

revision = "a23001"
down_revision = "d9a3f6c8e215"
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
        "messaging_channels",
        *_base(),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "kind"),
    )
    op.create_index("ix_messaging_channels_tenant_id", "messaging_channels", ["tenant_id"])
    op.create_index("ix_messaging_channels_created_at", "messaging_channels", ["created_at"])
    op.create_table(
        "messaging_conversations",
        *_base(),
        sa.Column("customer_id", GUID, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("recipient_enc", sa.Text(), nullable=False),
        sa.Column("recipient_masked", sa.String(200), nullable=False),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", "customer_id", "channel"),
    )
    op.create_index(
        "ix_messaging_conversations_tenant_id", "messaging_conversations", ["tenant_id"]
    )
    op.create_index(
        "ix_messaging_conversations_created_at", "messaging_conversations", ["created_at"]
    )
    op.create_table(
        "messaging_consents",
        *_base(),
        sa.Column(
            "conversation_id", GUID, sa.ForeignKey("messaging_conversations.id"), nullable=False
        ),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.String(500), nullable=False),
        sa.Column("actor_id", GUID, nullable=False),
    )
    op.create_index("ix_messaging_consents_tenant_id", "messaging_consents", ["tenant_id"])
    op.create_index("ix_messaging_consents_created_at", "messaging_consents", ["created_at"])
    op.create_table(
        "messaging_templates",
        *_base(),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("subject_en", sa.String(160), nullable=False),
        sa.Column("subject_bn", sa.String(160), nullable=False),
        sa.Column("body_en", sa.String(2000), nullable=False),
        sa.Column("body_bn", sa.String(2000), nullable=False),
        sa.UniqueConstraint("tenant_id", "key"),
    )
    op.create_index("ix_messaging_templates_tenant_id", "messaging_templates", ["tenant_id"])
    op.create_index("ix_messaging_templates_created_at", "messaging_templates", ["created_at"])
    op.create_table(
        "messaging_messages",
        *_base(),
        sa.Column(
            "conversation_id", GUID, sa.ForeignKey("messaging_conversations.id"), nullable=False
        ),
        sa.Column("order_id", GUID, sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("template_key", sa.String(80), nullable=False),
        sa.Column("locale", sa.String(2), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("first_attempt_at", TZDateTime(), nullable=True),
        sa.Column("next_attempt_at", TZDateTime(), nullable=False),
        sa.Column("provider_reference", sa.String(200), nullable=True),
        sa.Column("last_error", sa.String(80), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    op.create_index("ix_messaging_messages_tenant_id", "messaging_messages", ["tenant_id"])
    op.create_index("ix_messaging_messages_created_at", "messaging_messages", ["created_at"])
    op.create_table(
        "messaging_attempts",
        *_base(),
        sa.Column("message_id", GUID, sa.ForeignKey("messaging_messages.id"), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
    )
    op.create_index("ix_messaging_attempts_tenant_id", "messaging_attempts", ["tenant_id"])
    op.create_index("ix_messaging_attempts_created_at", "messaging_attempts", ["created_at"])
    op.create_index("ix_message_due", "messaging_messages", ["status", "next_attempt_at"])
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
            "messaging_channels",
            "messaging_conversations",
            "messaging_consents",
            "messaging_templates",
            "messaging_messages",
            "messaging_attempts",
        ]:
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("messaging_attempts")
    op.drop_table("messaging_messages")
    op.drop_table("messaging_templates")
    op.drop_table("messaging_consents")
    op.drop_table("messaging_conversations")
    op.drop_table("messaging_channels")
