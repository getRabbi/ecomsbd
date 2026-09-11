"""Model registry.

SQLAlchemy resolves relationship strings against classes that have actually been
imported. Importing one model module is therefore not enough — a relationship
pointing at an unimported class fails at first use, not at import.

Everything that maps a table is imported here, and application startup, Alembic
and the test suite all import this module rather than individual model files.
"""

from __future__ import annotations

from app.admin.models import PlatformAdmin, RepairActionRecord, SupportCase
from app.auth.models import AuthSession, Device, OtpChallenge, RefreshToken
from app.billing.models import (
    BillingAttempt,
    BillingProviderCustomer,
    BillingTransaction,
    BillingWebhookEvent,
    PlayPurchaseToken,
    SubscriptionEvent,
)
from app.common.audit import AuditLog
from app.common.feature_flags import FeatureFlag
from app.common.idempotency import IdempotencyKey
from app.common.outbox import OutboxEvent
from app.common.provider_health import ProviderHealth
from app.consignments.models import Consignment, ConsignmentItem
from app.couriers.models import (
    CourierAccount,
    CourierBookingAttempt,
    CourierEvent,
    CourierRawPayload,
    CourierReturnRequest,
    CourierSyncCursor,
    CourierWebhookDelivery,
    ProviderPayment,
)
from app.customers.models import Customer, CustomerAddress
from app.db.base import Base
from app.entitlements.models import Subscription
from app.entitlements.usage import UsageCounter
from app.expenses.models import Expense, ExpenseAllocation
from app.exports.models import ExportJob
from app.imports.models import ImportBatch, ImportRow
from app.ledger.models import LedgerEntry
from app.money.models import CodReceivable
from app.notifications.delivery import NotificationDelivery, NotificationPreference
from app.notifications.models import Notification
from app.orders.models import Order, OrderItem
from app.payouts.models import (
    Payout,
    PayoutAdjustment,
    PayoutLine,
    PayoutSourceFile,
)
from app.privacy.models import DeletionRequest
from app.products.models import Product, StockMovement
from app.profit.models import ConsignmentCharge, ProfitSnapshot
from app.reconciliation.models import ReconciliationCase
from app.sync.models import SyncMutation
from app.tenants.models import Tenant, TenantUser
from app.users.models import User

__all__ = [
    "AuditLog",
    "AuthSession",
    "Base",
    "BillingAttempt",
    "BillingProviderCustomer",
    "BillingTransaction",
    "BillingWebhookEvent",
    "CodReceivable",
    "Consignment",
    "ConsignmentCharge",
    "ConsignmentItem",
    "CourierAccount",
    "CourierBookingAttempt",
    "CourierEvent",
    "CourierRawPayload",
    "CourierReturnRequest",
    "CourierSyncCursor",
    "CourierWebhookDelivery",
    "Customer",
    "CustomerAddress",
    "DeletionRequest",
    "Device",
    "Expense",
    "ExpenseAllocation",
    "ExportJob",
    "FeatureFlag",
    "IdempotencyKey",
    "ImportBatch",
    "ImportRow",
    "LedgerEntry",
    "Notification",
    "NotificationDelivery",
    "NotificationPreference",
    "Order",
    "OrderItem",
    "OtpChallenge",
    "OutboxEvent",
    "Payout",
    "PayoutAdjustment",
    "PayoutLine",
    "PayoutSourceFile",
    "PlatformAdmin",
    "PlayPurchaseToken",
    "Product",
    "ProfitSnapshot",
    "ProviderHealth",
    "ProviderPayment",
    "ReconciliationCase",
    "RefreshToken",
    "RepairActionRecord",
    "StockMovement",
    "Subscription",
    "SubscriptionEvent",
    "SupportCase",
    "SyncMutation",
    "Tenant",
    "TenantUser",
    "UsageCounter",
    "User",
]
