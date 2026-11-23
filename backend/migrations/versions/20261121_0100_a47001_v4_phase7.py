"""V4 phase 7 (WP7.4): ``referral_codes`` (one per shop) and ``referrals`` (one
per referred shop, owned by the referrer)."""

from alembic import op
import sqlalchemy as sa
from app.db.types import GUID, TZDateTime

revision = "a47001"
down_revision = "a44001"
branch_labels = None
depends_on = None

_TABLES = ["referral_codes", "referrals"]


def _base_columns() -> list[sa.Column]:
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
        "referral_codes",
        *_base_columns(),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("created_by_user_id", GUID, nullable=True),
        sa.UniqueConstraint("tenant_id", name="uq_referral_codes_tenant"),
        sa.UniqueConstraint("code", name="uq_referral_codes_code"),
    )
    op.create_table(
        "referrals",
        *_base_columns(),
        sa.Column(
            "referral_code_id",
            GUID,
            sa.ForeignKey("referral_codes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "referred_tenant_id",
            GUID,
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("applied_by_user_id", GUID, nullable=True),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("void_reason", sa.String(40), nullable=True),
        sa.Column(
            "qualifying_submission_id",
            GUID,
            sa.ForeignKey("billing_payment_submissions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("rewarded_at", TZDateTime(), nullable=True),
        sa.Column("referred_credit_state", sa.String(12), nullable=False),
        sa.Column("referrer_credit_state", sa.String(12), nullable=False),
        sa.Column("referrer_hold_reason", sa.String(40), nullable=True),
        sa.UniqueConstraint("referred_tenant_id", name="uq_referrals_referred_tenant"),
        sa.UniqueConstraint("qualifying_submission_id", name="uq_referrals_submission"),
        sa.CheckConstraint("referred_tenant_id <> tenant_id", name="ck_referrals_not_self"),
        sa.CheckConstraint(
            "(state = 'PENDING' AND rewarded_at IS NULL)"
            " OR (state = 'REWARDED' AND rewarded_at IS NOT NULL"
            " AND qualifying_submission_id IS NOT NULL)"
            " OR (state = 'VOID' AND void_reason IS NOT NULL)",
            name="ck_referrals_state",
        ),
        sa.CheckConstraint(
            "(referrer_credit_state = 'HELD') = (referrer_hold_reason IS NOT NULL)",
            name="ck_referrals_hold_reason",
        ),
    )
    for table in _TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.create_index(f"ix_{table}_created_at", table, ["created_at"])

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
