"""Which committed table changes tell signed-in clients to refetch what.

A realtime event names a *topic*, never a record's contents. Clients map a
topic to the screens that show it and ask the API again, so the API's
permissions, tenant scoping and shapes stay the only way data reaches a screen.

Every mapped table is listed exactly once, either under a topic or as silent.
``tests/test_realtime.py`` fails when a new table is in neither, so adding a
model forces a decision about whether a client should hear about it.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "PRIMARY_TABLES",
    "SILENT_TABLES",
    "TABLE_TOPICS",
    "RealtimeTopic",
]


class RealtimeTopic(StrEnum):
    ORDERS = "orders"
    CUSTOMERS = "customers"
    PRODUCTS = "products"
    INVENTORY = "inventory"
    #: Courier accounts, consignments and their tracking.
    COURIERS = "couriers"
    #: COD receivables and the ledger behind every money figure.
    MONEY = "money"
    #: Courier payouts and their reconciliation.
    PAYOUTS = "payouts"
    EXPENSES = "expenses"
    NOTIFICATIONS = "notifications"
    #: Members, roles and invitations.
    TEAM = "team"
    #: The shop's own settings and plan.
    SHOP = "shop"


T = RealtimeTopic

TABLE_TOPICS: dict[str, RealtimeTopic] = {
    # orders
    "orders": T.ORDERS,
    "order_items": T.ORDERS,
    "external_orders": T.ORDERS,
    "chat_order_drafts": T.ORDERS,
    "order_confirmations": T.ORDERS,
    "confirmation_attempts": T.ORDERS,
    # V4 Autopilot decisions: the Home "Autopilot today" card counts them
    "autopilot_decisions": T.ORDERS,
    # customers
    "customers": T.CUSTOMERS,
    "customer_addresses": T.CUSTOMERS,
    "customer_activities": T.CUSTOMERS,
    "customer_followups": T.CUSTOMERS,
    "customer_tags": T.CUSTOMERS,
    "customer_tag_links": T.CUSTOMERS,
    # products
    "products": T.PRODUCTS,
    "product_variants": T.PRODUCTS,
    "suppliers": T.PRODUCTS,
    "supplier_items": T.PRODUCTS,
    # inventory
    "stock_movements": T.INVENTORY,
    "warehouses": T.INVENTORY,
    "warehouse_stock": T.INVENTORY,
    "purchase_orders": T.INVENTORY,
    "purchase_order_lines": T.INVENTORY,
    "goods_receipts": T.INVENTORY,
    "goods_receipt_lines": T.INVENTORY,
    "stock_transfers": T.INVENTORY,
    "stock_transfer_lines": T.INVENTORY,
    "supplier_payments": T.INVENTORY,
    "demand_forecasts": T.INVENTORY,
    # couriers
    "courier_accounts": T.COURIERS,
    "consignments": T.COURIERS,
    "consignment_items": T.COURIERS,
    "consignment_charges": T.COURIERS,
    "courier_events": T.COURIERS,
    "courier_return_requests": T.COURIERS,
    "courier_booking_attempts": T.COURIERS,
    # money
    "cod_receivables": T.MONEY,
    "financial_ledger_entries": T.MONEY,
    "profit_snapshots": T.MONEY,
    # payouts and reconciliation
    "payouts": T.PAYOUTS,
    "payout_lines": T.PAYOUTS,
    "payout_adjustments": T.PAYOUTS,
    "payout_source_files": T.PAYOUTS,
    "courier_provider_payments": T.PAYOUTS,
    "reconciliation_cases": T.PAYOUTS,
    "reconciliation_items": T.PAYOUTS,
    "reconciliation_case_events": T.PAYOUTS,
    # expenses
    "expenses": T.EXPENSES,
    "expense_allocations": T.EXPENSES,
    # notifications
    "notifications": T.NOTIFICATIONS,
    "notification_preferences": T.NOTIFICATIONS,
    # team
    "tenant_users": T.TEAM,
    "tenant_invitations": T.TEAM,
    # shop
    "tenants": T.SHOP,
    "subscriptions": T.SHOP,
    "network_preferences": T.SHOP,
    "shop_delivery_rules": T.SHOP,
    "shop_intake_settings": T.SHOP,
    # V4 manual TrxID payments: the Plans card lists the shop's submissions
    "billing_payment_submissions": T.SHOP,
    # V4 referrals: the Plans card shows the shop's code and its referrals
    "referral_codes": T.SHOP,
    "referrals": T.SHOP,
}

#: The table whose row *is* the topic's entity. A commit that changed exactly
#: one such row names it as ``entity_id``; child rows never do, so an order
#: with three lines is still one order.
PRIMARY_TABLES: dict[RealtimeTopic, str] = {
    T.ORDERS: "orders",
    T.CUSTOMERS: "customers",
    T.PRODUCTS: "products",
    T.COURIERS: "consignments",
    T.MONEY: "cod_receivables",
    T.PAYOUTS: "payouts",
    T.EXPENSES: "expenses",
    T.NOTIFICATIONS: "notifications",
}

#: Tables no client screen is refreshed for. Bookkeeping that changes on every
#: request (sessions, idempotency, usage counters), worker cursors and raw
#: provider traffic would otherwise refresh every open screen for nothing, and
#: auth and platform tables are never shop data at all.
SILENT_TABLES: frozenset[str] = frozenset(
    {
        # auth, users and platform administration
        "users",
        "auth_identities",
        "auth_sessions",
        "auth_tokens",
        "refresh_tokens",
        "otp_challenges",
        "devices",
        "platform_admins",
        "admin_repair_actions",
        "support_cases",
        "deletion_requests",
        "feature_flags",
        "provider_health",
        "network_benchmarks",
        "network_benchmark_cells",
        # infrastructure
        "audit_logs",
        "outbox_events",
        "idempotency_keys",
        "sync_mutations",
        "usage_counters",
        "export_jobs",
        # V4 funnel events: admin-only, no seller screen shows them
        "product_events",
        # V4 model-call accounting: admin/ops only, no seller screen
        "ai_usage",
        # V4 linked-device credentials: never shown in a seller list
        "device_ingest_keys",
        "device_pairing_codes",
        # V4 global device rules: admin-managed, devices poll them with an ETag
        "intake_rule_sets",
        # imports: committed rows arrive as orders, products or customers
        "imports",
        "import_rows",
        "import_mappings",
        # billing records behind the plan; the plan itself is `subscriptions`
        "billing_attempts",
        "billing_provider_customers",
        "billing_transactions",
        "billing_webhook_events",
        "subscription_events",
        "play_purchase_tokens",
        # courier transport bookkeeping
        "courier_raw_payloads",
        "courier_sync_cursors",
        "courier_webhook_deliveries",
        # notifications delivery attempts; the notification itself is mapped
        "notification_deliveries",
        # automation engine internals
        "automation_rules",
        "automation_workflow_versions",
        "automation_executions",
        "automation_step_runs",
        "automation_attempts",
        "automation_tasks",
        # chat capture; a confirmed draft becomes an order
        "chat_messages",
        "chat_identities",
        "chat_attention_items",
        # V4 SMS confirm-link tokens (hashed); the confirmation row is mapped
        "confirmation_links",
        # messaging and campaigns
        "messaging_channels",
        "messaging_templates",
        "messaging_conversations",
        "messaging_messages",
        "messaging_attempts",
        "messaging_consents",
        "messaging_campaigns",
        "messaging_campaign_recipients",
        # integrations and the public API
        "integration_connections",
        "integration_events",
        "integration_sync_runs",
        "integration_links",
        "integration_conflicts",
        "integration_order_states",
        "external_order_sources",
        "public_api_keys",
        "public_api_write_receipts",
        "outbound_webhook_endpoints",
        "outbound_webhook_deliveries",
        "outbound_webhook_attempts",
        # risk lookups are metered and audited reads, not shop data
        "external_risk_lookups",
        "risk_provider_connections",
        # V4 voice: provider traffic, metering and admin-managed audio; the
        # confirmation and its attempts (mapped above) are what screens show
        "voice_provider_connections",
        "voice_call_events",
        "voice_callback_deliveries",
        "voice_call_charges",
        "voice_credit_packs",
        "voice_daily_usage",
        "voice_intro_audio",
        # V4 WP7.1: the platform SMS gateway account (admin console only)
        "sms_provider_connections",
    }
)
