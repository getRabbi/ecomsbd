"""Automation Pro: published workflow versions, step receipts, waits and causation."""

import uuid
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime, JSONColumn

revision = "a34001"
down_revision = "a33001"
branch_labels = None
depends_on = None

_TABLES = ["automation_workflow_versions", "automation_step_runs"]


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
        "automation_workflow_versions",
        *_base(),
        sa.Column("rule_id", GUID, sa.ForeignKey("automation_rules.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(80), nullable=False),
        sa.Column("definition", JSONColumn, nullable=False),
        sa.Column("plan", JSONColumn, nullable=False),
        sa.Column("published_by", GUID, nullable=False),
        sa.UniqueConstraint("tenant_id", "rule_id", "number"),
    )
    _indexes("automation_workflow_versions")
    with op.batch_alter_table("automation_rules") as batch:
        batch.add_column(sa.Column("draft", JSONColumn, nullable=True))
        batch.add_column(sa.Column("published_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("recipe_key", sa.String(64), nullable=True))
    with op.batch_alter_table("automation_executions") as batch:
        batch.add_column(
            sa.Column(
                "version_id",
                GUID,
                sa.ForeignKey(
                    "automation_workflow_versions.id",
                    name="fk_automation_executions_version",
                ),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("trigger", sa.String(80), nullable=True))
        batch.add_column(sa.Column("cursor", sa.String(40), nullable=True))
        batch.add_column(sa.Column("retryable", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("depth", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("ancestry", JSONColumn, nullable=True))
        batch.add_column(sa.Column("parent_execution_id", GUID, nullable=True))
        batch.add_column(sa.Column("subject_key", sa.String(80), nullable=True))
        batch.add_column(sa.Column("source", sa.String(16), nullable=False, server_default="EVENT"))
        batch.add_column(sa.Column("wait_key", sa.String(120), nullable=True))
        batch.add_column(sa.Column("wait_satisfied_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("started_at", TZDateTime(), nullable=True))
        batch.add_column(sa.Column("finished_at", TZDateTime(), nullable=True))
    op.create_index("ix_automation_wait_key", "automation_executions", ["tenant_id", "wait_key"])
    op.create_index(
        "ix_automation_rule_subject",
        "automation_executions",
        ["tenant_id", "rule_id", "subject_key"],
    )
    with op.batch_alter_table("automation_attempts") as batch:
        batch.add_column(sa.Column("step_id", sa.String(40), nullable=True))
    op.create_table(
        "automation_step_runs",
        *_base(),
        sa.Column("execution_id", GUID, sa.ForeignKey("automation_executions.id"), nullable=False),
        sa.Column("step_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result_id", sa.String(80), nullable=True),
        sa.Column("outcome", sa.String(80), nullable=True),
        sa.Column("finished_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "execution_id", "step_id"),
    )
    _indexes("automation_step_runs")

    _publish_existing_rules()
    # V2 statuses become the V3.4 ones. A pending V2 run keeps its snapshot
    # and runs as a one-step plan starting at "s1".
    op.execute(
        "UPDATE automation_executions SET status = 'QUEUED', cursor = 's1' "
        "WHERE status IN ('PENDING', 'RETRY')"
    )
    op.execute(
        "UPDATE automation_executions SET status = 'SUCCEEDED', finished_at = updated_at "
        "WHERE status = 'DONE'"
    )
    op.execute(
        "UPDATE automation_executions SET finished_at = updated_at "
        "WHERE status IN ('SKIPPED', 'FAILED')"
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


def _publish_existing_rules():
    """Every V2 rule becomes version 1 of a one-step workflow, unchanged."""
    rules = sa.table(
        "automation_rules",
        sa.column("id", GUID),
        sa.column("tenant_id", GUID),
        sa.column("trigger", sa.String),
        sa.column("conditions", JSONColumn),
        sa.column("action", sa.String),
        sa.column("config", JSONColumn),
        sa.column("created_by", GUID),
        sa.column("draft", JSONColumn),
        sa.column("published_version", sa.Integer),
    )
    versions = sa.table(
        "automation_workflow_versions",
        sa.column("id", GUID),
        sa.column("tenant_id", GUID),
        sa.column("created_at", TZDateTime()),
        sa.column("updated_at", TZDateTime()),
        sa.column("rule_id", GUID),
        sa.column("number", sa.Integer),
        sa.column("trigger", sa.String),
        sa.column("definition", JSONColumn),
        sa.column("plan", JSONColumn),
        sa.column("published_by", GUID),
    )
    db = op.get_bind()
    now = datetime.now(UTC)
    for row in db.execute(sa.select(rules)).mappings().all():
        step = {"type": "action", "id": "s1", "action": row["action"], "config": row["config"]}
        definition = {
            "trigger": row["trigger"],
            "conditions": {"match": "all", "conditions": row["conditions"] or [], "groups": []},
            "steps": [step],
        }
        db.execute(
            versions.insert().values(
                id=uuid.uuid4(),
                tenant_id=row["tenant_id"],
                created_at=now,
                updated_at=now,
                rule_id=row["id"],
                number=1,
                trigger=row["trigger"],
                definition=definition,
                plan={"entry": "s1", "steps": {"s1": {**step, "next": None}}},
                published_by=row["created_by"],
            )
        )
        db.execute(
            rules.update()
            .where(rules.c.id == row["id"])
            .values(draft=definition, published_version=1)
        )


def downgrade():
    op.execute(
        "UPDATE automation_executions SET status = 'PENDING' WHERE status IN ('QUEUED', 'WAITING', 'RUNNING')"
    )
    op.execute("UPDATE automation_executions SET status = 'DONE' WHERE status = 'SUCCEEDED'")
    op.execute("UPDATE automation_executions SET status = 'SKIPPED' WHERE status = 'CANCELLED'")
    op.drop_table("automation_step_runs")
    with op.batch_alter_table("automation_attempts") as batch:
        batch.drop_column("step_id")
    op.drop_index("ix_automation_rule_subject", table_name="automation_executions")
    op.drop_index("ix_automation_wait_key", table_name="automation_executions")
    with op.batch_alter_table("automation_executions") as batch:
        batch.drop_constraint("fk_automation_executions_version", type_="foreignkey")
        for column in (
            "finished_at",
            "started_at",
            "wait_satisfied_at",
            "wait_key",
            "source",
            "subject_key",
            "parent_execution_id",
            "ancestry",
            "depth",
            "retryable",
            "cursor",
            "trigger",
            "version_id",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("automation_rules") as batch:
        for column in ("recipe_key", "published_version", "draft"):
            batch.drop_column(column)
    op.drop_table("automation_workflow_versions")
