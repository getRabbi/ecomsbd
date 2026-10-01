"""Chat-to-order: inbound Messenger/WhatsApp messages, sender identities, drafts, follow-ups."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, JSONColumn, TZDateTime

revision = "a39001"
down_revision = "a38001"
branch_labels = None
depends_on = None

_TABLES = ["chat_identities", "chat_order_drafts", "chat_messages", "chat_attention_items"]


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


def _connection():
    return sa.Column(
        "connection_id", GUID, sa.ForeignKey("integration_connections.id"), nullable=False
    )


def _identity():
    return sa.Column("identity_id", GUID, sa.ForeignKey("chat_identities.id"), nullable=False)


def upgrade():
    op.create_table(
        "chat_identities",
        *_base(),
        _connection(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_key", sa.String(64), nullable=False),
        sa.Column("external_id_enc", sa.Text(), nullable=False),
        sa.Column("account_ref", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("phone_masked", sa.String(24), nullable=True),
        sa.Column("customer_id", GUID, sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("customer_link_source", sa.String(20), nullable=True),
        sa.Column("customer_linked_at", TZDateTime(), nullable=True),
        sa.Column("last_message_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "connection_id", "external_key"),
    )
    _indexes("chat_identities")

    op.create_table(
        "chat_order_drafts",
        *_base(),
        _connection(),
        _identity(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("customer_id", GUID, nullable=True),
        sa.Column("customer_match", sa.String(16), nullable=False),
        sa.Column("customer_candidates", JSONColumn, nullable=False),
        sa.Column("customer_name", sa.String(160), nullable=True),
        sa.Column("phones", JSONColumn, nullable=False),
        sa.Column("selected_phone", sa.String(24), nullable=True),
        sa.Column("address", sa.String(1000), nullable=True),
        sa.Column("items", JSONColumn, nullable=False),
        sa.Column("cod_amount_paisa", sa.BigInteger(), nullable=True),
        sa.Column("cod_source", sa.String(12), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("warnings", JSONColumn, nullable=False),
        sa.Column("uncertain_fields", JSONColumn, nullable=False),
        sa.Column("missing_fields", JSONColumn, nullable=False),
        sa.Column("source_message_ids", JSONColumn, nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("attachment_count", sa.Integer(), nullable=False),
        sa.Column("first_message_at", TZDateTime(), nullable=False),
        sa.Column("last_message_at", TZDateTime(), nullable=False),
        sa.Column("ready_at", TZDateTime(), nullable=True),
        sa.Column("notified_at", TZDateTime(), nullable=True),
        sa.Column("closed_at", TZDateTime(), nullable=True),
        sa.Column("closed_by", GUID, nullable=True),
        sa.Column("confirmed_order_id", GUID, sa.ForeignKey("orders.id"), nullable=True),
    )
    _indexes("chat_order_drafts")
    op.create_index(
        "ix_chat_order_drafts_identity_status", "chat_order_drafts", ["identity_id", "status"]
    )
    op.create_index(
        "ix_chat_order_drafts_status_updated",
        "chat_order_drafts",
        ["tenant_id", "status", "updated_at"],
    )

    op.create_table(
        "chat_messages",
        *_base(),
        _connection(),
        _identity(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_message_id", sa.String(200), nullable=False),
        sa.Column("account_ref", sa.String(64), nullable=False),
        sa.Column("message_type", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("attachment_count", sa.Integer(), nullable=False),
        sa.Column("provider_timestamp", TZDateTime(), nullable=False),
        sa.Column("received_at", TZDateTime(), nullable=False),
        sa.Column("processing_status", sa.String(16), nullable=False),
        sa.Column("processing_code", sa.String(40), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("linked_customer_id", GUID, nullable=True),
        sa.Column("linked_draft_id", GUID, nullable=True),
        sa.Column("linked_attention_id", GUID, nullable=True),
        sa.UniqueConstraint("connection_id", "provider", "provider_message_id"),
    )
    _indexes("chat_messages")
    op.create_index("ix_chat_messages_due", "chat_messages", ["processing_status", "received_at"])
    op.create_index(
        "ix_chat_messages_identity", "chat_messages", ["identity_id", "provider_timestamp"]
    )

    op.create_table(
        "chat_attention_items",
        *_base(),
        _connection(),
        _identity(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("intent", sa.String(24), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("customer_id", GUID, nullable=True),
        sa.Column("order_id", GUID, sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("excerpt", sa.String(300), nullable=True),
        sa.Column("message_at", TZDateTime(), nullable=False),
        sa.Column("resolved_at", TZDateTime(), nullable=True),
        sa.Column("resolved_by", GUID, nullable=True),
    )
    _indexes("chat_attention_items")
    op.create_index(
        "ix_chat_attention_status", "chat_attention_items", ["tenant_id", "status", "created_at"]
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
