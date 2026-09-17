"""Courier accounts: per-account webhook secret and routing token.

Revision ID: f1a7c3b9e204
Revises: d73e9c5a1201

Pathao verifies its callbacks with a **per-merchant shared secret**, so unlike
Steadfast — whose webhook contract is still unknown — there is a real secret to
store, and a real need to know which shop a callback belongs to before its body
is trusted.

Two forward-only columns, both nullable, both meaningless for providers that do
not use them:

``webhook_secret_encrypted``
    The provider's signing secret, encrypted with the same vault and account
    context as the API credentials. Never returned to a client.

``webhook_token``
    An opaque, unguessable id that appears in the callback URL and selects the
    account. It is not a credential and proves nothing by itself; the account's
    secret is what verifies the delivery. Unique so a lookup by it is exact.

Nothing existing is altered. No index is added beyond the unique constraint the
lookup actually uses.
"""

from alembic import op
import sqlalchemy as sa

revision = "f1a7c3b9e204"
down_revision = "d73e9c5a1201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "courier_accounts",
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "courier_accounts",
        sa.Column("webhook_token", sa.String(length=64), nullable=True),
    )
    # A unique *index* rather than a table constraint: it gives the same
    # guarantee, it is the access path the callback route actually uses, and
    # unlike ALTER TABLE ... ADD CONSTRAINT it works on SQLite, which is what
    # the test suite migrates. NULL is not counted as a duplicate on either
    # backend, so accounts without a token coexist freely.
    op.create_index(
        "uq_courier_accounts_webhook_token",
        "courier_accounts",
        ["webhook_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_courier_accounts_webhook_token", table_name="courier_accounts")
    op.drop_column("courier_accounts", "webhook_token")
    op.drop_column("courier_accounts", "webhook_secret_encrypted")
