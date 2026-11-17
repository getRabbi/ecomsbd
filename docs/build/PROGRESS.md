# V4 build ledger
Spec: docs/V4_MASTER_BUILD_SPEC.md · Started: 2026-11-17 · Branch: v4/phase-0

## Now
WP: WP0.2 — Crash + funnel visibility   Step: explore (flags, tenancy/RLS pattern from a39001, admin ops router, event sources), then model + migration a40001
- Flag `product_events_enabled` (default off) gates emission; Sentry gated by non-empty `ECOMSBD_SENTRY_DSN`
- `app/product_events/`: model `product_events` (tenant-owned, RLS + revoke in a40001), service `record()` that never raises into the caller
- Dedupe: unique `(tenant_id, name, dedupe_key)`; `first_*` use a fixed key, `weekly_active` the ISO week, `plan_paid` the payment id
- Emit from existing services: shop_created, first_order, first_courier_connected, first_booking, first_payout_import, first_reconcile, plan_paid, weekly_active
- first_capture / first_confirmation_call / first_autopilot_order: names defined now, emitted by WP1.3 / WP2.x / WP4
- `GET /v1/admin/ops/funnel?from=&to=` (SUPPORT role): counts per step
- Flutter: `sentry_flutter`, init only with DSN, `sendDefaultPii=false`, `beforeSend` masks phones via `maskPhone`, drops message-text breadcrumbs
- Tests: dedupe, no PII in props, flag off = no rows, funnel counts, role check, cross-tenant; Flutter beforeSend unit test
- Docs: IMPLEMENTATION_STATUS row, runbook, `.env.example` for DSN

## Status
| WP | Status | Commit | Verified | Notes |
|---|---|---|---|---|
| WP0.1 | DONE | 90edda4 | backend 1805 passed/5 skipped, ruff+format+mypy clean; Flutter 541 (perf test flaky under load, green alone); web 66 | docs only: no flag/env/migration needed |
| WP0.2 | IN_PROGRESS | | | |
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
- 2026-11-17 env: `backend/.venv` points at another Windows user's Python; this machine uses `C:\Users\userw\.venvs\ecomsbd-backend` (uv, `pip install -e .[dev]`). Git runs with `-c safe.directory=E:/ecomsbd` (repo owned by another SID); no global config changed.

## Session log (newest first, one line each)
- 2026-11-17 WP0.1 package id + IMPLEMENTATION_STATUS top + ledger 90edda4
- 2026-11-17 setup: spec + run prompt committed on main (2c2783d, not pushed); v4/phase-0 created
