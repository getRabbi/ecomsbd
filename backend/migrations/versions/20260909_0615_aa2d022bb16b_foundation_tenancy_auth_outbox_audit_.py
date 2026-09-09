"""foundation: tenancy, auth, outbox, audit, flags, entitlements

Creates the Phase A schema:

*   tenants / tenant_users / users — multi-tenancy and membership.
*   otp_challenges / auth_sessions / refresh_tokens / devices —
    phone-OTP authentication with revocable, rotating sessions.
*   outbox_events — the durable transactional outbox.
*   audit_logs — append-only audit trail.
*   feature_flags — global, per-tenant and percentage rollout.
*   idempotency_keys — replay protection for money-touching endpoints.
*   subscriptions — plan state that entitlements resolve from.

Portable across PostgreSQL (production) and SQLite (test/local); the
PostgreSQL rendering uses native uuid, jsonb and timestamptz.

Revision ID: aa2d022bb16b
Revises:
Create Date: 2026-09-09 06:15:22.531352+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
import app.db.types

revision: str = "aa2d022bb16b"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.String(length=200), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=80), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("context", app.db.types.JSONColumn, nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("client_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_logs_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_created_at"), ["created_at"], unique=False)
        batch_op.create_index("ix_audit_logs_entity", ["entity_type", "entity_id"], unique=False)
        batch_op.create_index(
            "ix_audit_logs_tenant_created", ["tenant_id", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_audit_logs_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("rollout_percentage", sa.Integer(), nullable=False),
        sa.Column("payload", app.db.types.JSONColumn, nullable=False),
        sa.Column("description", sa.String(length=400), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.CheckConstraint(
            "rollout_percentage >= 0 AND rollout_percentage <= 100",
            name=op.f("ck_feature_flags_rollout_percentage_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feature_flags")),
        sa.UniqueConstraint("key", "tenant_id", name="uq_feature_flags_key_tenant_id"),
    )
    with op.batch_alter_table("feature_flags", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_feature_flags_key"), ["key"], unique=False)
        batch_op.create_index(batch_op.f("ix_feature_flags_tenant_id"), ["tenant_id"], unique=False)

    op.create_table(
        "otp_challenges",
        sa.Column("phone_search_hmac", sa.String(length=64), nullable=False),
        sa.Column("phone_enc", sa.Text(), nullable=False),
        sa.Column("phone_last4", sa.String(length=4), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("consumed_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("locked_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("delivery_status", sa.String(length=20), nullable=False),
        sa.Column("delivery_provider", sa.String(length=40), nullable=False),
        sa.Column("delivery_reference", sa.String(length=120), nullable=True),
        sa.Column("delivery_error", sa.String(length=400), nullable=True),
        sa.Column("request_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_otp_challenges_attempts_non_negative")),
        sa.CheckConstraint(
            "max_attempts > 0", name=op.f("ck_otp_challenges_max_attempts_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_otp_challenges")),
    )
    with op.batch_alter_table("otp_challenges", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_otp_challenges_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index("ix_otp_challenges_expires_at", ["expires_at"], unique=False)
        batch_op.create_index(
            "ix_otp_challenges_phone_created", ["phone_search_hmac", "created_at"], unique=False
        )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("payload", app.db.types.JSONColumn, nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("locked_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("processed_at", app.db.types.TZDateTime(), nullable=True),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_outbox_events_attempts_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("dedupe_key", name="uq_outbox_events_dedupe_key"),
    )
    with op.batch_alter_table("outbox_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_outbox_events_claimable", ["status", "available_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_outbox_events_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_outbox_events_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_outbox_events_topic"), ["topic"], unique=False)

    op.create_table(
        "tenants",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("business_category", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("order_number_prefix", sa.String(length=8), nullable=False),
        sa.Column("pickup_contact_name", sa.String(length=160), nullable=True),
        sa.Column("pickup_phone_enc", sa.Text(), nullable=True),
        sa.Column("pickup_phone_last4", sa.String(length=4), nullable=True),
        sa.Column("pickup_address_raw", sa.Text(), nullable=True),
        sa.Column("pickup_address_normalized", sa.Text(), nullable=True),
        sa.Column("pickup_district", sa.String(length=80), nullable=True),
        sa.Column("pickup_area", sa.String(length=120), nullable=True),
        sa.Column("onboarding_step", sa.String(length=32), nullable=False),
        sa.Column("onboarding_completed_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("settings", app.db.types.JSONColumn, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("deleted_at", app.db.types.TZDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tenants_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_tenants_status"), ["status"], unique=False)
        batch_op.create_index(
            "ix_tenants_status_created_at", ["status", "created_at"], unique=False
        )

    op.create_table(
        "users",
        sa.Column("phone_search_hmac", sa.String(length=64), nullable=False),
        sa.Column("phone_enc", sa.Text(), nullable=False),
        sa.Column("phone_last4", sa.String(length=4), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("last_login_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("deleted_at", app.db.types.TZDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("phone_search_hmac", name="uq_users_phone_search_hmac"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_users_phone_search_hmac"), ["phone_search_hmac"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_users_status"), ["status"], unique=False)

    op.create_table(
        "devices",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("install_id", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("os_version", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("push_token", sa.Text(), nullable=True),
        sa.Column("push_token_updated_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("last_seen_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("revoked_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_devices_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("user_id", "install_id", name="uq_devices_user_id_install_id"),
    )
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_devices_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_devices_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            "ix_devices_user_last_seen", ["user_id", "last_seen_at"], unique=False
        )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("endpoint", sa.String(length=200), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("response_body", app.db.types.JSONColumn, nullable=True),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("completed_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("expires_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_idempotency_keys_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint(
            "tenant_id", "key", "endpoint", name="uq_idempotency_keys_tenant_id_key_endpoint"
        ),
    )
    with op.batch_alter_table("idempotency_keys", schema=None) as batch_op:
        batch_op.create_index("ix_idempotency_keys_expires_at", ["expires_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_idempotency_keys_tenant_id"), ["tenant_id"], unique=False
        )

    op.create_table(
        "subscriptions",
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("current_period_start", app.db.types.TZDateTime(), nullable=True),
        sa.Column("current_period_end", app.db.types.TZDateTime(), nullable=True),
        sa.Column("cancelled_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("provider_reference", sa.String(length=200), nullable=True),
        sa.Column("provider_customer_reference", sa.String(length=200), nullable=True),
        sa.Column("verified_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("metadata_json", app.db.types.JSONColumn, nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("grant_reason", sa.String(length=400), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_subscriptions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
    )
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_subscriptions_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            "ix_subscriptions_current_period_end", ["current_period_end"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_subscriptions_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(
            "ix_subscriptions_tenant_status", ["tenant_id", "status"], unique=False
        )

    op.create_table(
        "tenant_users",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("joined_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_users_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tenant_users_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_users")),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_users_tenant_id_user_id"),
    )
    with op.batch_alter_table("tenant_users", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_tenant_users_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_tenant_users_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tenant_users_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            "ix_tenant_users_user_id_tenant_id", ["user_id", "tenant_id"], unique=False
        )

    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("client_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("last_seen_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("expires_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("revoked_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(length=40), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("updated_at", app.db.types.TZDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_auth_sessions_device_id_devices"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_auth_sessions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_auth_sessions_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_auth_sessions_tenant_id"), ["tenant_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_auth_sessions_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            "ix_auth_sessions_user_revoked", ["user_id", "revoked_at"], unique=False
        )

    op.create_table(
        "refresh_tokens",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("expires_at", app.db.types.TZDateTime(), nullable=False),
        sa.Column("used_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", app.db.types.TZDateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(length=40), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_refresh_tokens_session_id_auth_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_refresh_tokens_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            "ix_refresh_tokens_session_issued", ["session_id", "issued_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.drop_index("ix_refresh_tokens_session_issued")
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_session_id"))

    op.drop_table("refresh_tokens")
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_auth_sessions_user_revoked")
        batch_op.drop_index(batch_op.f("ix_auth_sessions_user_id"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_auth_sessions_created_at"))

    op.drop_table("auth_sessions")
    with op.batch_alter_table("tenant_users", schema=None) as batch_op:
        batch_op.drop_index("ix_tenant_users_user_id_tenant_id")
        batch_op.drop_index(batch_op.f("ix_tenant_users_user_id"))
        batch_op.drop_index(batch_op.f("ix_tenant_users_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_tenant_users_created_at"))

    op.drop_table("tenant_users")
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.drop_index("ix_subscriptions_tenant_status")
        batch_op.drop_index(batch_op.f("ix_subscriptions_tenant_id"))
        batch_op.drop_index("ix_subscriptions_current_period_end")
        batch_op.drop_index(batch_op.f("ix_subscriptions_created_at"))

    op.drop_table("subscriptions")
    with op.batch_alter_table("idempotency_keys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_idempotency_keys_tenant_id"))
        batch_op.drop_index("ix_idempotency_keys_expires_at")

    op.drop_table("idempotency_keys")
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_index("ix_devices_user_last_seen")
        batch_op.drop_index(batch_op.f("ix_devices_user_id"))
        batch_op.drop_index(batch_op.f("ix_devices_created_at"))

    op.drop_table("devices")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_status"))
        batch_op.drop_index(batch_op.f("ix_users_phone_search_hmac"))
        batch_op.drop_index(batch_op.f("ix_users_created_at"))

    op.drop_table("users")
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.drop_index("ix_tenants_status_created_at")
        batch_op.drop_index(batch_op.f("ix_tenants_status"))
        batch_op.drop_index(batch_op.f("ix_tenants_created_at"))

    op.drop_table("tenants")
    with op.batch_alter_table("outbox_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_outbox_events_topic"))
        batch_op.drop_index(batch_op.f("ix_outbox_events_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_outbox_events_status"))
        batch_op.drop_index("ix_outbox_events_claimable")

    op.drop_table("outbox_events")
    with op.batch_alter_table("otp_challenges", schema=None) as batch_op:
        batch_op.drop_index("ix_otp_challenges_phone_created")
        batch_op.drop_index("ix_otp_challenges_expires_at")
        batch_op.drop_index(batch_op.f("ix_otp_challenges_created_at"))

    op.drop_table("otp_challenges")
    with op.batch_alter_table("feature_flags", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_feature_flags_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_feature_flags_key"))

    op.drop_table("feature_flags")
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_logs_tenant_id"))
        batch_op.drop_index("ix_audit_logs_tenant_created")
        batch_op.drop_index("ix_audit_logs_entity")
        batch_op.drop_index(batch_op.f("ix_audit_logs_created_at"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_action"))

    op.drop_table("audit_logs")
