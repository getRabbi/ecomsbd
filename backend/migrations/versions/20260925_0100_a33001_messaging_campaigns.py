"""Messaging & campaigns: marketing consent, campaigns, recipients, provider receipts."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime, JSONColumn

revision = "a33001"
down_revision = "a32002"
branch_labels = None
depends_on = None

_TABLES = ["messaging_campaigns", "messaging_campaign_recipients"]


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
        "messaging_campaigns",
        *_base(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("flow", sa.String(24), nullable=True),
        sa.Column("flow_days", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("template_key", sa.String(80), nullable=False),
        sa.Column("locale", sa.String(2), nullable=False),
        sa.Column("audience", JSONColumn, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scheduled_at", TZDateTime(), nullable=True),
        sa.Column("started_at", TZDateTime(), nullable=True),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column("cancelled_at", TZDateTime(), nullable=True),
        sa.Column("last_run_at", TZDateTime(), nullable=True),
        sa.Column("last_error", sa.String(64), nullable=True),
        sa.Column("rate_per_minute", sa.Integer(), nullable=False),
        sa.Column("frequency_cap_hours", sa.Integer(), nullable=False),
        sa.Column("attribution_days", sa.Integer(), nullable=False),
        sa.Column("total_recipients", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", GUID, nullable=False),
        sa.Column("launched_by", GUID, nullable=True),
    )
    _indexes("messaging_campaigns")
    op.create_index("ix_campaign_due", "messaging_campaigns", ["status", "scheduled_at"])
    op.create_table(
        "messaging_campaign_recipients",
        *_base(),
        sa.Column("campaign_id", GUID, sa.ForeignKey("messaging_campaigns.id"), nullable=False),
        sa.Column("customer_id", GUID, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column(
            "conversation_id", GUID, sa.ForeignKey("messaging_conversations.id"), nullable=True
        ),
        sa.Column("cycle_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("skip_reason", sa.String(32), nullable=True),
        sa.Column("message_id", GUID, sa.ForeignKey("messaging_messages.id"), nullable=True),
        sa.Column("due_at", TZDateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "campaign_id", "customer_id", "cycle_key"),
    )
    _indexes("messaging_campaign_recipients")
    op.create_index(
        "ix_campaign_recipient_due",
        "messaging_campaign_recipients",
        ["campaign_id", "status", "due_at"],
    )

    with op.batch_alter_table("messaging_conversations") as batch:
        batch.add_column(sa.Column("recipient_hash", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("marketing_consent_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("marketing_opted_out_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("last_marketing_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("undeliverable_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("undeliverable_reason", sa.String(40), nullable=True))
        batch.add_column(sa.Column("unsubscribe_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("unsubscribe_enc", sa.Text(), nullable=True))
        batch.create_unique_constraint(
            "uq_messaging_conversation_unsubscribe", ["unsubscribe_hash"]
        )
    op.create_index(
        "ix_messaging_conversation_recipient",
        "messaging_conversations",
        ["tenant_id", "channel", "recipient_hash"],
    )

    with op.batch_alter_table("messaging_consents") as batch:
        batch.add_column(
            sa.Column("scope", sa.String(16), nullable=False, server_default="TRANSACTIONAL")
        )
        batch.add_column(
            sa.Column("source", sa.String(24), nullable=False, server_default="SELLER")
        )
        batch.add_column(sa.Column("campaign_id", GUID, nullable=True))
        batch.alter_column("actor_id", existing_type=GUID, nullable=True)

    with op.batch_alter_table("messaging_templates") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(16), nullable=False, server_default="TRANSACTIONAL")
        )
        batch.add_column(
            sa.Column("channel", sa.String(16), nullable=False, server_default="EMAIL")
        )
        batch.add_column(sa.Column("provider_name", sa.String(512), nullable=True))
        batch.add_column(sa.Column("provider_language_bn", sa.String(16), nullable=True))
        batch.add_column(sa.Column("provider_language_en", sa.String(16), nullable=True))
        batch.add_column(sa.Column("provider_category", sa.String(24), nullable=True))
        batch.add_column(sa.Column("provider_status", sa.String(24), nullable=True))
        batch.add_column(sa.Column("provider_checked_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("variables", JSONColumn, nullable=True))
        batch.add_column(
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("messaging_messages") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(16), nullable=False, server_default="TRANSACTIONAL")
        )
        batch.add_column(
            sa.Column("channel", sa.String(16), nullable=False, server_default="EMAIL")
        )
        batch.add_column(
            sa.Column("campaign_id", GUID, sa.ForeignKey("messaging_campaigns.id"), nullable=True)
        )
        batch.add_column(sa.Column("provider", sa.String(24), nullable=True))
        batch.add_column(sa.Column("params", JSONColumn, nullable=True))
        batch.add_column(sa.Column("sent_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("delivered_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("read_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("failed_at", TZDateTime(), nullable=True))
    op.create_index(
        "ix_message_campaign", "messaging_messages", ["tenant_id", "campaign_id", "status"]
    )
    op.create_index(
        "ix_message_provider_ref", "messaging_messages", ["provider", "provider_reference"]
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
    op.drop_index("ix_message_provider_ref", table_name="messaging_messages")
    op.drop_index("ix_message_campaign", table_name="messaging_messages")
    with op.batch_alter_table("messaging_messages") as batch:
        for column in (
            "failed_at",
            "read_at",
            "delivered_at",
            "sent_at",
            "params",
            "provider",
            "campaign_id",
            "channel",
            "purpose",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("messaging_templates") as batch:
        for column in (
            "archived",
            "variables",
            "provider_checked_at",
            "provider_status",
            "provider_category",
            "provider_language_en",
            "provider_language_bn",
            "provider_name",
            "channel",
            "purpose",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("messaging_consents") as batch:
        batch.drop_column("campaign_id")
        batch.drop_column("source")
        batch.drop_column("scope")
    op.drop_index("ix_messaging_conversation_recipient", table_name="messaging_conversations")
    with op.batch_alter_table("messaging_conversations") as batch:
        batch.drop_constraint("uq_messaging_conversation_unsubscribe", type_="unique")
        for column in (
            "unsubscribe_enc",
            "unsubscribe_hash",
            "undeliverable_reason",
            "undeliverable_at",
            "last_marketing_at",
            "marketing_opted_out_at",
            "marketing_consent_at",
            "marketing_consent",
            "recipient_hash",
        ):
            batch.drop_column(column)
    op.drop_table("messaging_campaign_recipients")
    op.drop_table("messaging_campaigns")
