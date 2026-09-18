"""Saved import mappings, and the fields an async commit needs.

Revision ID: a4d92f1c60b8
Revises: c8e41d7b93f5

**``import_mappings``.** Sellers import the same weekly export, from the same
courier panel, with the same oddly-named columns. Re-mapping it every time is
the difference between a feature they use and one they try once. Unique on
(tenant, template, name) so saving twice updates rather than accumulating
near-duplicates a seller then has to choose between.

**Async commit fields on ``imports``.** A five-thousand-row commit creates five
thousand orders through the normal order service, which is far too long to hold
an HTTP request open. Large imports hand off to the ARQ worker, and these three
columns are what make that observable and resumable:

``job_id``          the enqueued job, so a stuck import can be traced.
``processed_count`` how far a running commit has got, for progress.
``started_at``      when the commit began, so a crashed one can be identified
                    rather than sitting in COMMITTING forever with no clue when
                    it stopped.
"""

from alembic import op
import sqlalchemy as sa

revision = "a4d92f1c60b8"
down_revision = "c8e41d7b93f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("template", sa.String(length=32), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("source_headers", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "template", "name", name="uq_import_mappings_tenant_template_name"
        ),
    )
    op.create_index(
        "ix_import_mappings_tenant_template",
        "import_mappings",
        ["tenant_id", "template"],
    )

    op.add_column("imports", sa.Column("job_id", sa.String(length=64), nullable=True))
    op.add_column(
        "imports",
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "imports", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("imports", "started_at")
    op.drop_column("imports", "processed_count")
    op.drop_column("imports", "job_id")
    op.drop_index("ix_import_mappings_tenant_template", table_name="import_mappings")
    op.drop_table("import_mappings")
