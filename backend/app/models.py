"""Model registry.

SQLAlchemy resolves relationship strings against classes that have actually been
imported. Importing one model module is therefore not enough — a relationship
pointing at an unimported class fails at first use, not at import.

Everything that maps a table is imported here, and application startup, Alembic
and the test suite all import this module rather than individual model files.
"""

from __future__ import annotations

from app.auth.models import AuthSession, Device, OtpChallenge, RefreshToken
from app.common.audit import AuditLog
from app.common.feature_flags import FeatureFlag
from app.common.idempotency import IdempotencyKey
from app.common.outbox import OutboxEvent
from app.consignments.models import Consignment, ConsignmentItem
from app.customers.models import Customer, CustomerAddress
from app.db.base import Base
from app.entitlements.models import Subscription
from app.expenses.models import Expense, ExpenseAllocation
from app.imports.models import ImportBatch, ImportRow
from app.ledger.models import LedgerEntry
from app.money.models import CodReceivable
from app.notifications.models import Notification
from app.orders.models import Order, OrderItem
from app.payouts.models import (
    Payout,
    PayoutAdjustment,
    PayoutLine,
    PayoutSourceFile,
)
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
    "CodReceivable",
    "Consignment",
    "ConsignmentCharge",
    "ConsignmentItem",
    "Customer",
    "CustomerAddress",
    "Device",
    "Expense",
    "ExpenseAllocation",
    "FeatureFlag",
    "IdempotencyKey",
    "ImportBatch",
    "ImportRow",
    "LedgerEntry",
    "Notification",
    "Order",
    "OrderItem",
    "OtpChallenge",
    "OutboxEvent",
    "Payout",
    "PayoutAdjustment",
    "PayoutLine",
    "PayoutSourceFile",
    "Product",
    "ProfitSnapshot",
    "ReconciliationCase",
    "RefreshToken",
    "StockMovement",
    "Subscription",
    "SyncMutation",
    "Tenant",
    "TenantUser",
    "User",
]
