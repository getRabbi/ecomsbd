"""Central tenant isolation.

Master spec section 47 requires a *mandatory* tenant-scoped session pattern, not
a filter that each developer remembers to add. This module installs four
independent guards on every :class:`~sqlalchemy.orm.Session` created by the app:

1.  **Read filter** — every ORM ``SELECT`` touching a :class:`~app.db.base.TenantOwned`
    entity gets ``WHERE tenant_id = :active_tenant`` injected via
    ``with_loader_criteria``, including relationship loads and aliases.
2.  **Materialisation check** — every tenant-owned object loaded into the session
    is verified against the active tenant. This catches anything that reaches the
    ORM through a path the read filter did not rewrite.
3.  **Write guard** — on flush, inserts are stamped with the active tenant, and
    updates or deletes of another tenant's row are rejected before SQL is sent.
4.  **Raw-SQL guard** — a Core (non-ORM) ``SELECT`` against a tenant-owned table
    is refused unless it explicitly declares that it applies its own tenant
    filter. Closed by default, so a hand-written analytics query cannot silently
    skip isolation.

Deliberate cross-tenant work (workers, admin repair tooling, the login lookup
that discovers which tenants a phone number belongs to) must opt in through
:func:`system_session` or :func:`allow_cross_tenant`, both of which log the
reason. A cross-tenant read is a P0 incident class (master spec section 117), so
the escape hatches are noisy on purpose.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import Select, TextClause, event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.core.context import current_tenant_id
from app.core.errors import TenantIsolationError
from app.core.logging import get_logger
from app.db.base import Base, TenantOwned

__all__ = [
    "TENANT_CHECKED",
    "allow_cross_tenant",
    "install_tenancy_guards",
    "is_cross_tenant_allowed",
    "mark_session_system",
    "session_is_system",
    "tenant_owned_table_names",
]

log = get_logger(__name__)

#: Execution option a hand-written statement sets to declare that it applies its
#: own tenant filter: ``session.execute(stmt, execution_options={TENANT_CHECKED: True})``.
TENANT_CHECKED = "ecomsbd_tenant_checked"

#: ``Session.info`` key marking a session as deliberately unscoped.
_SYSTEM_SCOPE_KEY = "ecomsbd_system_scope"

_cross_tenant_reason: ContextVar[str | None] = ContextVar(
    "ecomsbd_cross_tenant_reason", default=None
)

_warned_text_statements: set[int] = set()


# --------------------------------------------------------------------------- #
# Escape hatches
# --------------------------------------------------------------------------- #


def mark_session_system(session: Session, reason: str) -> None:
    """Mark a session as deliberately cross-tenant (workers, admin tooling)."""
    session.info[_SYSTEM_SCOPE_KEY] = reason


def session_is_system(session: Session) -> bool:
    return _SYSTEM_SCOPE_KEY in session.info


@contextmanager
def allow_cross_tenant(reason: str) -> Iterator[None]:
    """Temporarily disable tenant scoping for a narrow, audited operation.

    The reason is logged at ``WARNING`` so every exemption is visible in
    production logs and can be reviewed. Keep the block as small as possible.
    """
    log.warning("tenant scope bypass", extra={"bypass_reason": reason})
    token = _cross_tenant_reason.set(reason)
    try:
        yield
    finally:
        _cross_tenant_reason.reset(token)


def is_cross_tenant_allowed() -> bool:
    return _cross_tenant_reason.get() is not None


# --------------------------------------------------------------------------- #
# Introspection
# --------------------------------------------------------------------------- #


def tenant_owned_table_names() -> frozenset[str]:
    """Table names backing :class:`TenantOwned` models.

    Computed from the mapper registry rather than a hand-maintained list, so a
    new tenant-owned table is protected the moment it is declared.
    """
    names: set[str] = set()
    for mapper in Base.registry.mappers:
        if issubclass(mapper.class_, TenantOwned):
            names.update(table.name for table in mapper.tables)
    return frozenset(names)


def _active_tenant_or_none(session: Session) -> uuid.UUID | None:
    """Tenant to enforce, or ``None`` when this session is deliberately unscoped."""
    if session_is_system(session) or is_cross_tenant_allowed():
        return None
    return current_tenant_id()


def _statement_touches_tenant_tables(statement: Select[Any]) -> list[str]:
    try:
        froms = statement.get_final_froms()
    except Exception:
        return []
    owned = tenant_owned_table_names()
    names = (getattr(source, "name", None) for source in froms)
    return sorted({name for name in names if name in owned})


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def _guard_orm_execute(state: ORMExecuteState) -> None:
    """Guards 1 and 4: filter ORM reads, refuse unscoped raw reads."""
    if not state.is_select or state.is_column_load:
        return

    session = state.session
    if session_is_system(session) or is_cross_tenant_allowed():
        return
    if state.execution_options.get(TENANT_CHECKED):
        return

    tenant_id = current_tenant_id()

    if state.is_orm_statement:
        if tenant_id is None:
            # Reading tenant-owned entities with no tenant in scope is a bug.
            # Entities that are not tenant-owned (users, tenants, flags) are
            # unaffected because the criteria only matches TenantOwned.
            state.statement = state.statement.options(
                with_loader_criteria(
                    TenantOwned,
                    lambda cls: cls.tenant_id.is_(None),
                    include_aliases=True,
                )
            )
            return
        state.statement = state.statement.options(
            with_loader_criteria(
                TenantOwned,
                lambda cls: cls.tenant_id == tenant_id,
                include_aliases=True,
            )
        )
        return

    statement = state.statement
    if isinstance(statement, TextClause):
        key = id(statement)
        if key not in _warned_text_statements:
            _warned_text_statements.add(key)
            log.warning(
                "raw textual SQL executed on a tenant-scoped session; "
                "tenant filtering is the caller's responsibility",
                extra={"tenant_id": str(tenant_id) if tenant_id else None},
            )
        return

    if isinstance(statement, Select):
        touched = _statement_touches_tenant_tables(statement)
        if touched:
            raise TenantIsolationError(
                "Core SELECT against tenant-owned table(s) "
                f"{touched} without tenant scoping. Use ORM entities, or pass "
                f"execution_options={{'{TENANT_CHECKED}': True}} after adding an "
                "explicit tenant_id filter.",
                active_tenant_id=tenant_id,
                entity=",".join(touched),
            )


def _guard_loaded_object(session: Session, instance: object) -> None:
    """Guard 2: verify every tenant-owned object materialised into the session."""
    if not isinstance(instance, TenantOwned):
        return
    if session_is_system(session) or is_cross_tenant_allowed():
        return
    active = current_tenant_id()
    if active is None:
        return
    owner = getattr(instance, "tenant_id", None)
    if owner is not None and owner != active:
        raise TenantIsolationError(
            f"Loaded {type(instance).__name__} belonging to another tenant",
            requested_tenant_id=owner,
            active_tenant_id=active,
            entity=type(instance).__name__,
        )


def _guard_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Guard 3: stamp inserts, reject cross-tenant writes."""
    if session_is_system(session) or is_cross_tenant_allowed():
        return

    pending = [
        obj
        for bucket in (session.new, session.dirty, session.deleted)
        for obj in bucket
        if isinstance(obj, TenantOwned)
    ]
    if not pending:
        return

    active = current_tenant_id()
    if active is None:
        entities = sorted({type(obj).__name__ for obj in pending})
        raise TenantIsolationError(
            "Refusing to write tenant-owned rows with no tenant in scope: "
            f"{entities}. Use system_session() for deliberate cross-tenant work.",
            entity=",".join(entities),
        )

    for obj in session.new:
        if isinstance(obj, TenantOwned):
            owner = getattr(obj, "tenant_id", None)
            if owner is None:
                obj.tenant_id = active
            elif owner != active:
                raise TenantIsolationError(
                    f"Refusing to insert {type(obj).__name__} for another tenant",
                    requested_tenant_id=owner,
                    active_tenant_id=active,
                    entity=type(obj).__name__,
                )

    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, TenantOwned):
            continue
        owner = getattr(obj, "tenant_id", None)
        if owner != active:
            raise TenantIsolationError(
                f"Refusing to modify {type(obj).__name__} owned by another tenant",
                requested_tenant_id=owner,
                active_tenant_id=active,
                entity=type(obj).__name__,
            )
        # Reassigning tenant_id would move a row between tenants.
        state = obj.__dict__.get("_sa_instance_state")
        if state is not None:
            attr_history = state.attrs["tenant_id"].history
            if attr_history.deleted and attr_history.deleted[0] != active:
                raise TenantIsolationError(
                    f"Refusing to reassign {type(obj).__name__}.tenant_id",
                    requested_tenant_id=attr_history.deleted[0],
                    active_tenant_id=active,
                    entity=type(obj).__name__,
                )


_INSTALLED = False


def install_tenancy_guards() -> None:
    """Attach the guards to the global Session class. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "do_orm_execute", _guard_orm_execute)
    event.listen(Session, "loaded_as_persistent", _guard_loaded_object)
    event.listen(Session, "before_flush", _guard_flush)
    _INSTALLED = True
