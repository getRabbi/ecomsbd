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
from app.db.base import Base
from app.entitlements.models import Subscription
from app.tenants.models import Tenant, TenantUser
from app.users.models import User

__all__ = [
    "AuditLog",
    "AuthSession",
    "Base",
    "Device",
    "FeatureFlag",
    "IdempotencyKey",
    "OtpChallenge",
    "OutboxEvent",
    "RefreshToken",
    "Subscription",
    "Tenant",
    "TenantUser",
    "User",
]
