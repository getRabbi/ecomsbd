"""Seller CRM notes, tags and follow-ups."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime

revision = "d9a3f6c8e215"
down_revision = "c7d2e9f4a816"
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


def _customer():
    return sa.Column(
        "customer_id", GUID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )


def upgrade():
    op.create_table(
        "customer_activities",
        *_base(),
        _customer(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("actor_id", GUID, nullable=True),
        sa.Column("subject_id", GUID, nullable=True),
    )
    op.create_table(
        "customer_tags",
        *_base(),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("name_key", sa.String(120), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name_key"),
    )
    op.create_table(
        "customer_tag_links",
        *_base(),
        _customer(),
        sa.Column(
            "tag_id", GUID, sa.ForeignKey("customer_tags.id", ondelete="CASCADE"), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "customer_id", "tag_id"),
    )
    op.create_table(
        "customer_followups",
        *_base(),
        _customer(),
        sa.Column("text", sa.String(2000), nullable=False),
        sa.Column("due_at", TZDateTime(), nullable=False),
        sa.Column("assignee_id", GUID, nullable=True),
        sa.Column("created_by", GUID, nullable=False),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.Column("completed_by", GUID, nullable=True),
    )
    for table in (
        "customer_activities",
        "customer_tags",
        "customer_tag_links",
        "customer_followups",
    ):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.create_index(f"ix_{table}_created_at", table, ["created_at"])
    op.create_index(
        "ix_crm_activity_customer_page",
        "customer_activities",
        ["tenant_id", "customer_id", "created_at", "id"],
    )
    op.create_index(
        "ix_crm_tag_customers", "customer_tag_links", ["tenant_id", "tag_id", "customer_id"]
    )
    op.create_index(
        "ix_crm_followup_due", "customer_followups", ["tenant_id", "completed_at", "due_at"]
    )
    op.create_index(
        "ix_crm_followup_customer",
        "customer_followups",
        ["tenant_id", "customer_id", "created_at", "id"],
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
        for table in (
            "customer_activities",
            "customer_tags",
            "customer_tag_links",
            "customer_followups",
        ):
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    for table in (
        "customer_followups",
        "customer_tag_links",
        "customer_tags",
        "customer_activities",
    ):
        op.drop_table(table)
