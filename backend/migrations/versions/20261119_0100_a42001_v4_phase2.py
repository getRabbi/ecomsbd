"""V4 phase 2: customer order confirmations, attempts and SMS links (WP2.1)."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, JSONColumn, TZDateTime

revision = "a42001"
down_revision = "a41001"
branch_labels = None
depends_on = None

_TABLES = ["order_confirmations", "confirmation_attempts", "confirmation_links"]

# One open confirmation per order. Partial unique index: PostgreSQL and SQLite
# both support the WHERE clause.
_OPEN_PREDICATE = "state NOT IN ('CONFIRMED', 'CANCELLED', 'SUPERSEDED')"


def _base_columns():
    return [
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "tenant_id", GUID, sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", TZDateTime(), nullable=False),
        sa.Column("updated_at", TZDateTime(), nullable=False),
    ]


def _base_indexes(table):
    op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    op.create_index(f"ix_{table}_created_at", table, ["created_at"])


def upgrade():
    op.create_table(
        "order_confirmations",
        *_base_columns(),
        sa.Column(
            "order_id", GUID, sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column("reason_code", sa.String(32), nullable=True),
        sa.Column("facts", JSONColumn, nullable=True),
        sa.Column("method", sa.String(16), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_action_at", TZDateTime(), nullable=True),
        sa.Column("requested_by", GUID, nullable=True),
        sa.Column("resolved_at", TZDateTime(), nullable=True),
        sa.Column("resolved_by", GUID, nullable=True),
        sa.Column("superseded_by", GUID, nullable=True),
        sa.Column("order_version", sa.Integer(), nullable=False),
        sa.Column("cod_paisa_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("order_outcome", sa.String(16), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_order_confirmations_attempt_count_non_negative"
        ),
        sa.CheckConstraint(
            "cod_paisa_snapshot >= 0", name="ck_order_confirmations_cod_snapshot_non_negative"
        ),
    )
    _base_indexes("order_confirmations")
    op.create_index(
        "uq_order_confirmations_open_order",
        "order_confirmations",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text(_OPEN_PREDICATE),
        sqlite_where=sa.text(_OPEN_PREDICATE),
    )
    op.create_index(
        "ix_order_confirmations_order", "order_confirmations", ["order_id", "created_at"]
    )
    op.create_index(
        "ix_order_confirmations_due",
        "order_confirmations",
        ["tenant_id", "state", "next_action_at"],
    )

    op.create_table(
        "confirmation_attempts",
        *_base_columns(),
        sa.Column(
            "confirmation_id",
            GUID,
            sa.ForeignKey("order_confirmations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("provider_ref", sa.String(120), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("dtmf", sa.String(4), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("cost_paisa", sa.BigInteger(), nullable=True),
        sa.Column("cost_quality", sa.String(12), nullable=True),
        sa.Column("started_at", TZDateTime(), nullable=False),
        sa.Column("ended_at", TZDateTime(), nullable=True),
        sa.UniqueConstraint(
            "confirmation_id", "attempt_no", name="uq_confirmation_attempts_confirmation_no"
        ),
        sa.CheckConstraint("attempt_no >= 1", name="ck_confirmation_attempts_attempt_no_positive"),
        sa.CheckConstraint(
            "cost_paisa IS NULL OR cost_paisa >= 0",
            name="ck_confirmation_attempts_cost_non_negative",
        ),
    )
    _base_indexes("confirmation_attempts")

    op.create_table(
        "confirmation_links",
        *_base_columns(),
        sa.Column(
            "confirmation_id",
            GUID,
            sa.ForeignKey("order_confirmations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TZDateTime(), nullable=False),
        sa.Column("used_at", TZDateTime(), nullable=True),
        sa.Column("result", sa.String(12), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_confirmation_links_token_hash"),
    )
    _base_indexes("confirmation_links")
    op.create_index("ix_confirmation_links_confirmation", "confirmation_links", ["confirmation_id"])

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
