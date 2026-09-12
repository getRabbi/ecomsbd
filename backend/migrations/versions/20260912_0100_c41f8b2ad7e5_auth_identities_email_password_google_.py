"""auth identities: email/password, Google and Apple sign-in

Adds the identity model that lets one ecomsbd user hold several ways of
signing in, and makes the phone columns on ``users`` optional.

The phone change is the one worth reading twice. Until now a user *was* a phone
number: the column was NOT NULL and uniquely indexed. A seller who registers
with an email has no phone, and writing a placeholder into that column would
put fabricated numbers into the field customer lookup matches on. So the three
columns become nullable. The unique constraint stays — SQL treats NULLs as
distinct, so many phoneless users coexist while two users still cannot share a
number.

Nothing is dropped and no existing row changes. Every OTP account keeps working
exactly as it did; ``auth_identities`` starts empty and is backfilled lazily by
whichever sign-in a user next performs.

Revision ID: c41f8b2ad7e5
Revises: a5b2552fea6c
Create Date: 2026-09-12 01:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.db.types

revision: str = "c41f8b2ad7e5"
down_revision: str | None = "a5b2552fea6c"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- users: a phone is no longer what identifies a person ---------------
    # batch_alter_table so SQLite gets the same result as PostgreSQL: SQLite
    # cannot ALTER a column in place and needs the table rebuilt.
    with op.batch_alter_table("users") as batch:
        batch.alter_column("phone_search_hmac", existing_type=sa.String(64), nullable=True)
        batch.alter_column("phone_enc", existing_type=sa.Text(), nullable=True)
        batch.alter_column("phone_last4", existing_type=sa.String(4), nullable=True)

    # --- auth_identities ----------------------------------------------------
    op.create_table(
        "auth_identities",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        # For PASSWORD this is the normalized email; for GOOGLE and APPLE the
        # provider's `sub`; for PHONE the phone search HMAC. No clear-text
        # phone number reaches this column.
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("normalized_email", sa.String(254), nullable=True),
        sa.Column(
            "email_verified", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("password_updated_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("last_login_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_auth_identities_provider_subject"
        ),
        sa.UniqueConstraint("user_id", "provider", name="uq_auth_identities_user_provider"),
    )
    op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])
    op.create_index("ix_auth_identities_provider", "auth_identities", ["provider"])
    op.create_index("ix_auth_identities_email", "auth_identities", ["normalized_email"])

    # --- auth_tokens: verify-this-email and reset-this-password links -------
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "identity_id",
            sa.Uuid(),
            sa.ForeignKey("auth_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        # Hashed, like a refresh token. Storing these in clear text would make
        # the table a list of live account-takeover keys.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("issued_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("expires_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("consumed_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("invalidated_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("request_ip_hash", sa.String(64), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
    )
    op.create_index("ix_auth_tokens_identity_id", "auth_tokens", ["identity_id"])
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])
    op.create_index(
        "ix_auth_tokens_identity_purpose", "auth_tokens", ["identity_id", "purpose"]
    )
    op.create_index("ix_auth_tokens_expires_at", "auth_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_tokens_expires_at", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_identity_purpose", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_user_id", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_identity_id", table_name="auth_tokens")
    op.drop_table("auth_tokens")

    op.drop_index("ix_auth_identities_email", table_name="auth_identities")
    op.drop_index("ix_auth_identities_provider", table_name="auth_identities")
    op.drop_index("ix_auth_identities_user_id", table_name="auth_identities")
    op.drop_table("auth_identities")

    # Restoring NOT NULL would fail against any row created by an email,
    # Google or Apple sign-in, which is correct: those rows genuinely have no
    # phone number, and inventing one to satisfy the constraint would be worse
    # than the downgrade refusing. Delete them first if this must run.
    with op.batch_alter_table("users") as batch:
        batch.alter_column("phone_last4", existing_type=sa.String(4), nullable=False)
        batch.alter_column("phone_enc", existing_type=sa.Text(), nullable=False)
        batch.alter_column("phone_search_hmac", existing_type=sa.String(64), nullable=False)
