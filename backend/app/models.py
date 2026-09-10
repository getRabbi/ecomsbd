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
from app.imports.models import ImportBatch, ImportRow
from app.ledger.models import LedgerEntry
from app.money.models import CodReceivable
from app.orders.models import Order, OrderItem
from app.products.models import Product, StockMovement
from app.sync.models import SyncMutation
from app.tenants.models import Tenant, TenantUser
from app.users.models import User

__all__ = [
    "AuditLog",
    "AuthSession",
    "Base",
    "CodReceivable",
    "Consignment",
    "ConsignmentItem",
    "Customer",
    "CustomerAddress",
    "Device",
    "FeatureFlag",
    "IdempotencyKey",
    "ImportBatch",
    "ImportRow",
    "LedgerEntry",
    "Order",
    "OrderItem",
    "OtpChallenge",
    "OutboxEvent",
    "Product",
    "RefreshToken",
    "StockMovement",
    "Subscription",
    "SyncMutation",
    "Tenant",
    "TenantUser",
    "User",
]
