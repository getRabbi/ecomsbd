"""Bounded automation rules, durable executions and seller tasks."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime, JSONColumn

revision = "a23006"
down_revision = "a23005"
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
        "automation_rules",
        *_base(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("trigger", sa.String(80), nullable=False),
        sa.Column("conditions", JSONColumn, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("config", JSONColumn, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", GUID, nullable=False),
    )
    op.create_index("ix_automation_rules_tenant_id", "automation_rules", ["tenant_id"])
    op.create_index("ix_automation_rules_created_at", "automation_rules", ["created_at"])
    op.create_table(
        "automation_executions",
        *_base(),
        sa.Column("rule_id", GUID, sa.ForeignKey("automation_rules.id"), nullable=False),
        sa.Column("event_id", GUID, nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSONColumn, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TZDateTime(), nullable=False),
        sa.Column("last_error", sa.String(80), nullable=True),
        sa.Column("result_id", sa.String(80), nullable=True),
        sa.UniqueConstraint("tenant_id", "rule_id", "event_id"),
    )
    op.create_index("ix_automation_executions_tenant_id", "automation_executions", ["tenant_id"])
    op.create_index("ix_automation_executions_created_at", "automation_executions", ["created_at"])
    op.create_table(
        "automation_attempts",
        *_base(),
        sa.Column("execution_id", GUID, sa.ForeignKey("automation_executions.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.String(80), nullable=True),
    )
    op.create_index("ix_automation_attempts_tenant_id", "automation_attempts", ["tenant_id"])
    op.create_index("ix_automation_attempts_created_at", "automation_attempts", ["created_at"])
    op.create_table(
        "automation_tasks",
        *_base(),
        sa.Column("execution_id", GUID, sa.ForeignKey("automation_executions.id"), nullable=False),
        sa.Column("order_id", GUID, sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("customer_id", GUID, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("text_en", sa.String(1000), nullable=False),
        sa.Column("text_bn", sa.String(1000), nullable=False),
        sa.Column("due_at", TZDateTime(), nullable=False),
        sa.Column("completed_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "execution_id"),
    )
    op.create_index("ix_automation_tasks_tenant_id", "automation_tasks", ["tenant_id"])
    op.create_index("ix_automation_tasks_created_at", "automation_tasks", ["created_at"])
    op.create_index("ix_automation_due", "automation_executions", ["status", "next_attempt_at"])
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
            "automation_rules",
            "automation_executions",
            "automation_attempts",
            "automation_tasks",
        ]:
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM {grantees}")
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("automation_tasks")
    op.drop_table("automation_attempts")
    op.drop_table("automation_executions")
    op.drop_table("automation_rules")
