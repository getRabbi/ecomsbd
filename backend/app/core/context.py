"""Ambient request context.

Holds the identifiers that every log line, audit row and tenant-scoped query
needs, without threading them through every function signature:

*   ``trace_id`` / ``request_id`` — correlation across API, worker and Sentry;
*   ``tenant_id`` — consumed by ``app.db.tenancy`` to scope *every* ORM query;
*   ``user_id`` / ``role`` — the acting principal, recorded on audit entries.

The tenant value here is the single input to tenant isolation. It is set in one
place (the authentication dependency) and read in one place (the SQLAlchemy
event hooks), so no feature module can forget to filter by tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from enum import StrEnum

__all__ = [
    "ActorType",
    "RequestContext",
    "clear_context",
    "current_context",
    "current_tenant_id",
    "require_tenant_id",
    "set_context",
    "use_context",
]


class ActorType(StrEnum):
    """Who performed an action. Recorded on every audit entry."""

    USER = "USER"
    SYSTEM = "SYSTEM"
    ADMIN = "ADMIN"
    PROVIDER = "PROVIDER"
    ANONYMOUS = "ANONYMOUS"


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable snapshot of the ambient request/job identity."""

    trace_id: str
    request_id: str | None = None
    tenant_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    actor_type: ActorType = ActorType.ANONYMOUS
    role: str | None = None
    device_id: uuid.UUID | None = None
    app_version: str | None = None
    client_ip: str | None = None
    job_name: str | None = None

    def log_fields(self) -> dict[str, str]:
        """Non-sensitive identifiers attached to every structured log line."""
        fields = {
            "trace_id": self.trace_id,
            "actor_type": str(self.actor_type),
        }
        optional = {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "role": self.role,
            "app_version": self.app_version,
            "job_name": self.job_name,
        }
        fields.update({key: str(value) for key, value in optional.items() if value is not None})
        return fields


_EMPTY = RequestContext(trace_id="-")

_context: ContextVar[RequestContext] = ContextVar("ecomsbd_request_context", default=_EMPTY)


def current_context() -> RequestContext:
    """The ambient context, or an empty one outside a request/job."""
    return _context.get()


def set_context(context: RequestContext) -> Token[RequestContext]:
    """Install a context. Callers are responsible for resetting the token."""
    return _context.set(context)


def clear_context(token: Token[RequestContext]) -> None:
    _context.reset(token)


def current_tenant_id() -> uuid.UUID | None:
    """Tenant in scope, if any."""
    return _context.get().tenant_id


def require_tenant_id() -> uuid.UUID:
    """Tenant in scope, or raise.

    Raised as ``LookupError`` rather than an HTTP error because reaching this
    without a tenant is a programming mistake, not a client mistake.
    """
    tenant_id = _context.get().tenant_id
    if tenant_id is None:
        raise LookupError(
            "No tenant in the current context. A tenant-scoped operation ran outside "
            "an authenticated request; use system_session() for deliberate "
            "cross-tenant work."
        )
    return tenant_id


@contextmanager
def use_context(**overrides: object) -> Iterator[RequestContext]:
    """Temporarily layer values onto the ambient context.

    Used by workers processing one tenant's outbox entries, and by tests.
    """
    base = _context.get()
    updated = replace(base, **overrides)  # type: ignore[arg-type]
    token = _context.set(updated)
    try:
        yield updated
    finally:
        _context.reset(token)
