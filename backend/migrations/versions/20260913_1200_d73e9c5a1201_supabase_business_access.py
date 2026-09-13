"""Deny Supabase client roles direct access to business tables.

Revision ID: d73e9c5a1201
Revises: c41f8b2ad7e5
"""

from alembic import op
import sqlalchemy as sa

revision = "d73e9c5a1201"
down_revision = "c41f8b2ad7e5"
branch_labels = None
depends_on = None

BUSINESS_TABLES = (
    "admin_repair_actions",
    "audit_logs",
    "auth_identities",
    "auth_sessions",
    "auth_tokens",
    "billing_attempts",
    "billing_provider_customers",
    "billing_transactions",
    "billing_webhook_events",
    "cod_receivables",
    "consignment_charges",
    "consignment_items",
    "consignments",
    "courier_accounts",
    "courier_booking_attempts",
    "courier_events",
    "courier_provider_payments",
    "courier_raw_payloads",
    "courier_return_requests",
    "courier_sync_cursors",
    "courier_webhook_deliveries",
    "customer_addresses",
    "customers",
    "deletion_requests",
    "devices",
    "expense_allocations",
    "expenses",
    "export_jobs",
    "feature_flags",
    "financial_ledger_entries",
    "idempotency_keys",
    "import_rows",
    "imports",
    "notification_deliveries",
    "notification_preferences",
    "notifications",
    "order_items",
    "orders",
    "otp_challenges",
    "outbox_events",
    "payout_adjustments",
    "payout_lines",
    "payout_source_files",
    "payouts",
    "platform_admins",
    "play_purchase_tokens",
    "products",
    "profit_snapshots",
    "provider_health",
    "reconciliation_cases",
    "refresh_tokens",
    "stock_movements",
    "subscription_events",
    "subscriptions",
    "support_cases",
    "sync_mutations",
    "tenant_users",
    "tenants",
    "usage_counters",
    "users",
)


def upgrade() -> None:
    db = op.get_bind()
    if db.dialect.name != "postgresql":
        return
    roles = (
        db.execute(
            sa.text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
        )
        .scalars()
        .all()
    )
    # PUBLIC can otherwise re-grant access inherited by anon/authenticated.
    grantees = ", ".join(["PUBLIC", *['"' + role + '"' for role in roles]])
    quote = db.dialect.identifier_preparer.quote
    for table in BUSINESS_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{quote(table)} FROM {grantees}")
        op.execute(f"ALTER TABLE public.{quote(table)} ENABLE ROW LEVEL SECURITY")
    # Future migrations by this same database owner inherit the same boundary.
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {grantees}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {grantees}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM {grantees}"
    )


def downgrade() -> None:
    # A rollback must never grant mobile clients access to seller business data.
    pass
