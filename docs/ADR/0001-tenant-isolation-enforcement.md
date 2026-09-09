# ADR 0001 — Tenant isolation is enforced by the ORM session, not by repositories

Status: Accepted · 2026-09-09 · Phase A

## Context

Master spec section 47 requires a **mandatory** tenant-scoped repository/session
pattern, and section 117 lists cross-tenant data exposure as a P0 incident. The
obvious implementation — "every query filters by `tenant_id`" — fails the moment
one developer forgets one filter, and nothing about the code makes that failure
visible until a seller sees another seller's money.

## Decision

Tenant scoping is installed once on the SQLAlchemy `Session` class
(`backend/app/db/tenancy.py`) as four independent guards:

1. **Read filter** — `do_orm_execute` injects
   `with_loader_criteria(TenantOwned, tenant_id == active)` into every ORM
   SELECT, including relationship loads and aliases.
2. **Materialisation check** — `loaded_as_persistent` verifies every
   tenant-owned object that reaches the identity map.
3. **Write guard** — `before_flush` stamps inserts with the active tenant and
   refuses updates, deletes or `tenant_id` reassignment of another tenant's row.
4. **Raw-SQL guard** — a Core (non-ORM) SELECT against a tenant-owned table is
   refused unless it declares `TENANT_CHECKED`.

Inheriting `TenantOwned` is the entire opt-in. Deliberate cross-tenant work uses
`system_session(reason)` or `allow_cross_tenant(reason)`, both of which log.

## Alternatives considered

**PostgreSQL row-level security.** The strongest option, and the right eventual
answer. Rejected for Phase A because it requires the application to connect as a
non-owner role with `SET LOCAL` per transaction, and because it cannot run in
the SQLite-backed test suite — which would mean the isolation tests, the ones
that matter most, would not run in CI or on a laptop. Recorded as planned
defence-in-depth; adding it later does not change any application code.

**Per-repository filtering.** Rejected: it is exactly the discipline-dependent
pattern the spec forbids.

**A custom `Session` subclass with scoped query methods.** Rejected: it only
protects the queries that go through those methods; `session.get()`,
`session.merge()` and relationship loads bypass it.

## Consequences

- A new tenant-owned table is protected the moment it inherits `TenantOwned`.
- A Core SELECT on a tenant table now fails loudly. This is intentional friction.
- Every exemption is greppable (`allow_cross_tenant`, `system_session`) and
  appears in production logs with its reason.
- Known limit: `session.get()` returning an object already in the identity map
  emits no query. Within a request the session is tenant-fixed and guard 2 has
  already validated everything in it, so the invariant holds; a session that
  deliberately mixes scopes is a `system_session` and is unguarded by design.

## Migration / rollback

No schema change. Removing the guards would silently disable isolation, so they
are covered by `backend/tests/test_tenant_isolation.py`, which fails closed.
