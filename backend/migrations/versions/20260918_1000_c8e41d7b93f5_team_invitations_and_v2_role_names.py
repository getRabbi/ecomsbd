"""Team invitations, and the V2.1 role names.

Revision ID: c8e41d7b93f5
Revises: f1a7c3b9e204

Two forward-only changes.

**1. ``tenant_invitations``.** V1 added a member the moment an owner typed a
number: the person became active before they had agreed to anything. V2.1 makes
that an offer the invitee accepts. The invitation is keyed on the phone number,
not on a mailed token — sign-in is OTP on a Bangladeshi mobile, so the number is
already the identity and a token would add a second secret to leak without
proving anything the OTP does not. The number is stored the way ``users`` stores
it: an HMAC to look it up by, ciphertext to show, last four to recognise.

**2. ``PACKER`` -> ``ORDER_OPERATOR``, ``ACCOUNTANT`` -> ``FINANCE``.** The two
middle roles keep their permissions exactly; only the names change, to the ones
V2.1 specifies. The code accepts both spellings — ``TenantRole`` keeps the old
names as aliases — so this data migration is about making stored rows say what
the UI says, not about making them work. It is written as two narrow UPDATEs
against known literals, and the downgrade restores them.
"""

from alembic import op
import sqlalchemy as sa

revision = "c8e41d7b93f5"
down_revision = "f1a7c3b9e204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("phone_search_hmac", sa.String(length=64), nullable=False),
        sa.Column("phone_enc", sa.Text(), nullable=False),
        sa.Column("phone_last4", sa.String(length=4), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # The shop's own listing, and the invitee's "which shops are waiting for
    # me?" — asked before any tenant is known, so it is keyed on the phone
    # hash alone.
    op.create_index(
        "ix_tenant_invitations_tenant_phone",
        "tenant_invitations",
        ["tenant_id", "phone_search_hmac"],
    )
    op.create_index(
        "ix_tenant_invitations_phone_status",
        "tenant_invitations",
        ["phone_search_hmac", "status"],
    )

    _rename_roles(
        (("PACKER", "ORDER_OPERATOR"), ("ACCOUNTANT", "FINANCE")),
    )


def downgrade() -> None:
    _rename_roles(
        (("ORDER_OPERATOR", "PACKER"), ("FINANCE", "ACCOUNTANT")),
    )
    op.drop_index("ix_tenant_invitations_phone_status", table_name="tenant_invitations")
    op.drop_index("ix_tenant_invitations_tenant_phone", table_name="tenant_invitations")
    op.drop_table("tenant_invitations")


def _rename_roles(pairs: tuple[tuple[str, str], ...]) -> None:
    """Rewrite stored role names, one narrow UPDATE per pair.

    Bound parameters rather than interpolation, and only the two literals this
    migration knows about — a role value it has never heard of is left exactly
    as it is rather than being guessed at.
    """
    connection = op.get_bind()
    statement = sa.text(
        "UPDATE tenant_users SET role = :new WHERE role = :old"
    )
    for old, new in pairs:
        connection.execute(statement, {"old": old, "new": new})
