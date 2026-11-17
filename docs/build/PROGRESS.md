# V4 build ledger
Spec: docs/V4_MASTER_BUILD_SPEC.md · Started: 2026-11-17 · Branch: v4/phase-0

## Now
WP: WP0.2 — Crash + funnel visibility   Step: slice 2 — wire `ProductEventService.record()` into existing services (after their own flush): shop_created (`app/tenants/service.py` create_shop), first_order (orders create), first_courier_connected (courier account connect/activate), first_booking (`app/couriers/booking.py` book success), first_payout_import (`app/payouts/service.py` import_statement), first_reconcile (`reconcile`), plan_paid (billing activation; dedupe = payment ref), weekly_active (order create + booking). One test per emit point.
- DONE slice 1 (390b18b): model + a40001 + service + `GET /v1/admin/ops/funnel` + 18 tests; flag `product_events_enabled` default off
- Slice 3: Flutter `sentry_flutter` (DSN define `ECOMSBD_SENTRY_DSN`, `sendDefaultPii=false`, `beforeSend` masks phones with `maskPhone` in `lib/core/money.dart`, drops breadcrumbs with message text) + unit test; flutter analyze/test/format
- Slice 4: `.env.example`/Flutter env README for the DSN, `docs/runbooks/product-events.md`, IMPLEMENTATION_STATUS row; full backend suite + ruff + format + mypy; push
- first_capture / first_confirmation_call / first_autopilot_order: names exist; emitted by WP1.3 / WP2.x / WP4

## Status
| WP | Status | Commit | Verified | Notes |
|---|---|---|---|---|
| WP0.1 | DONE | 90edda4 | backend 1805 passed/5 skipped, ruff+format+mypy clean; Flutter 541 (perf test flaky under load, green alone); web 66 | docs only: no flag/env/migration needed |
| WP0.2 | IN_PROGRESS | 390b18b | slice 1: 18 new tests + migrations/admin tests green; ruff/format/mypy clean | emits, Flutter Sentry, docs remain |
| WP0.3 | TODO | | | |
| WP0.4 | TODO | | | |
| WP0.5 | TODO | | | |
| WP0.6 | TODO | | | |
| WP0.7 | TODO | | | |
| WP1.1 | TODO | | | |
| WP1.2 | TODO | | | |
| WP1.3 | TODO | | | |
| WP1.4 | TODO | | | |
| WP1.5 | TODO | | | |
| WP2.1 | TODO | | | |
| WP2.2 | TODO | | | |
| WP2.3 | TODO | | | |
| WP2.4 | TODO | | | |
| WP2.5 | TODO | | | |
| WP3.1 | TODO | | | |
| WP3.2 | TODO | | | |
| WP3.3 | TODO | | | |
| WP3.4 | TODO | | | |
| WP4   | TODO | | | |
| WP5   | TODO | | | |
| WP6   | TODO | | | |
| WP7.1 | TODO | | | |
| WP7.2 | TODO | | | |
| WP7.3 | TODO | | | |
| WP7.4 | TODO | | | |

Statuses: TODO · IN_PROGRESS · DONE · BLOCKED: <CODE> · PARTIAL: <what remains>

## Gates
| Gate | Met? | Evidence |
|---|---|---|
| G0 | | |
| G1 | | |
| G2 | | |
| G3 | | |
| G4 | | |
| G5 | | |

## Operator actions waiting (things only Rabbi can do)
- [ ] ...

## Decisions made during build (spec was silent)
- 2026-11-17 setup: local `main` was 44 commits behind `origin/main`; fast-forwarded it (no local commits lost) before adding the spec, so phase branches start from current code.
- 2026-11-17 setup: the ledger is committed on `v4/phase-0`, not `main`, so `main` only carries the setup commit.
- 2026-11-17 WP0.1: `docs/IMPLEMENTATION_STATUS.md` also had a stale `com.smply.app` row; fixed it with a history note (file not on the WP list, but same fact).
- 2026-11-17 WP0.2: dedupe via unique `(tenant_id, name, dedupe_key)` (key `once` for shop_created/first_*, ISO week for weekly_active, payment ref for plan_paid) — gives the spec's unique `(tenant_id, name)` for first_* while letting repeatable events coexist, portable on SQLite + PostgreSQL.
- 2026-11-17 WP0.2: `props` accept only ints, bools and short codes (start with a letter, no 5+ digit run, ≤8 keys); anything else is dropped silently — the conservative reading of "no PII".
- 2026-11-17 WP0.2: `weekly_active` is emitted from order creation and booking (the core loop), not from every request — GET requests do not commit, so a per-request write would be lost or add load.
- 2026-11-17 WP0.2: funnel window = Asia/Dhaka days `[from, to]` inclusive, max 366 days; counts distinct shops per step. Permission `OPS_READ` (SUPPORT, OPS, SUPERADMIN).
- 2026-11-17 WP0.2: pre-existing, not caused by V4: `tests/test_tenant_isolation.py::TestCommerceCoreIsolation::test_stock_movements_are_not_visible_across_shops` fails when run alone (`NoReferencedTableError` warehouses — import order); passes in the full suite. Left untouched.
- 2026-11-17 env: `backend/.venv` points at another Windows user's Python; this machine uses `C:\Users\userw\.venvs\ecomsbd-backend` (uv, `pip install -e .[dev]`). Git runs with `-c safe.directory=E:/ecomsbd` (repo owned by another SID); no global config changed.

## Session log (newest first, one line each)
- 2026-11-17 WP0.2 slice 1: product_events + a40001 + funnel endpoint 390b18b; checkpoint (context)
- 2026-11-17 WP0.1 package id + IMPLEMENTATION_STATUS top + ledger 90edda4
- 2026-11-17 setup: spec + run prompt committed on main (2c2783d, not pushed); v4/phase-0 created
