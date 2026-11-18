# V4 build ledger
Spec: docs/V4_MASTER_BUILD_SPEC.md · Started: 2026-11-17 · Branch: v4/phase-0

## Now
WP: WP0.3 — LLM order parser (`EnhancedOrderParser`)   Step: slice 2 — plan items 3–9 (AiParseClient, schema, merge, ai_usage, service, API, config, eval --live, docs). Slice 1 (items 1–2) done.
Next-session notes: `tests/test_parser.py::...test_the_enhanced_parser_refuses_rather_than_pretending` asserts the old stub raises — replace it with the new boundary test (no client → deterministic + `AI_PARSE_UNAVAILABLE`); `eval.py` `--live` currently exits 2 until `enhanced.py`/`ai_client.py` exist; add the recorded-LLM G0.3 test to `tests/test_parse_corpus.py` (FakeAiParseClient.for_case, `llm_error`).
Plan:
1. Deterministic: `label-`/`label ` separators (`name-`, `address-`, `number`), district gazetteer (64, EN+BN) so a lone district line is an address (MEDIUM), one-line messages split into segments; `district`/`thana` on `ParsedOrder` + `ParseResponse` (optional).
2. Corpus `tests/fixtures/parse_corpus/*.yaml`: 5 seed + 45 cases, each with expected fields and a recorded LLM response; deterministic run always, LLM path with a fake client; G0.3 ≥ 90% on phone/address/items.
3. `AiParseClient` port + `AnthropicParseClient` (httpx Messages API, forced single extract tool, no acting tools) + `FakeAiParseClient`.
4. Pydantic `LlmParseOutput` (extra=forbid, bounded); any violation → discard LLM result.
5. Merge: LLM fills empty fields; replaces only when deterministic confidence < 0.5. LLM phones must normalise and appear in the source text.
6. `ai_usage` table (tenant-owned, RLS, grants revoked) added to phase-0 migration a40001; cost from `AI_PARSE_PRICE_*_PER_MTOK_USD` (estimate).
7. `EnhancedOrderParser.parse(...)` async: flag `ai_parse_enabled` + quota `ai_parse_monthly` (UsageCounter), Redis cache SHA-256(normalised text) per tenant 24 h, outage/timeout/quota → deterministic + `AI_PARSE_UNAVAILABLE`.
8. Wire `POST /v1/orders/parse` to it; config + `.env.example` + `.env.production.example`.
9. `python -m app.orders.parser.eval --live` (operator-only), runbook `docs/runbooks/ai-parse.md`, IMPLEMENTATION_STATUS row.

## Status
| WP | Status | Commit | Verified | Notes |
|---|---|---|---|---|
| WP0.1 | DONE | 90edda4 | backend 1805 passed/5 skipped, ruff+format+mypy clean; Flutter 541 (perf test flaky under load, green alone); web 66 | docs only: no flag/env/migration needed |
| WP0.2 | DONE | 58144bd | backend 1830 passed/5 skipped (25 new), ruff+format+mypy clean (319 files); Flutter analyze + format clean, 548 tests (7 new) with the pre-existing timing flake below; migration tests green on SQLite | PostgreSQL suite + a40001 round trip run at phase end |
| WP0.3 | IN_PROGRESS | 85463f8 | slice 1: corpus 50 cases, deterministic 95.8% gated (phones 50/50, address 47/50, items 41/44); parser+chat-order+order tests green | slice 2 (LLM path) next |
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
- [ ] WP0.2 (in progress — DSN being created): create the Sentry project, put its DSN in `apps/mobile_flutter/env/production.json` as `ECOMSBD_SENTRY_DSN`, build, and confirm one test event arrives with phones masked.
- [ ] WP0.2 (in progress — operator turning it on): turn `product_events_enabled` on (admin console, global) when ready to collect the funnel; check `GET /v1/admin/ops/funnel`.
- [x] Perf test decision (2026-11-18): keep every budget; tag `perf`, run alone in CI with `--concurrency=1`. Done.

## Decisions made during build (spec was silent)
- 2026-11-18 operator: `test/performance/*` tagged `@Tags(['perf'])` (declared in `apps/mobile_flutter/dart_test.yaml`); CI runs `flutter test --exclude-tags perf` then `flutter test --tags perf --concurrency=1`. Budgets unchanged. `codemagic.yaml` runs no `flutter test`, so unchanged.
- 2026-11-18 WP0.3: required fields for "call the LLM" = phone, address, items (what a booking needs); name is optional, so a missing name alone never costs a model call.
- 2026-11-18 WP0.3: a lone district line is an address at LOW (0.25) with a warning, so the seller completes it and an LLM answer may replace it; district + area is MEDIUM. Districts match on word boundaries (NFC, nukta variants).
- 2026-11-18 WP0.3: a price on a product line becomes `unit_price_paisa` only when the quantity is 1 (case 4); with quantity > 1 it is ambiguous and left empty.
- 2026-11-18 WP0.3: corpus `llm` blocks are authored fixtures in the exact tool schema, not live captures — said in the corpus header; live accuracy is the operator's `--live` run.
- 2026-11-18 WP0.3: short address hints (`rd`, `po`, ≤ 3 chars) now match whole words only — "order" and "Polash" were read as addresses (pre-existing bug found by the corpus).
- 2026-11-18 operator: `test_tenant_isolation` alone-run failure fixed at the root: the model registry `app/models.py` (imported by conftest) was missing `app.forecasting.models` and `app.procurement.models`; both added. No test changed.
- 2026-11-17 setup: local `main` was 44 commits behind `origin/main`; fast-forwarded it (no local commits lost) before adding the spec, so phase branches start from current code.
- 2026-11-17 setup: the ledger is committed on `v4/phase-0`, not `main`, so `main` only carries the setup commit.
- 2026-11-17 WP0.1: `docs/IMPLEMENTATION_STATUS.md` also had a stale `com.smply.app` row; fixed it with a history note (file not on the WP list, but same fact).
- 2026-11-17 WP0.2: dedupe via unique `(tenant_id, name, dedupe_key)` (key `once` for shop_created/first_*, ISO week for weekly_active, payment ref for plan_paid) — gives the spec's unique `(tenant_id, name)` for first_* while letting repeatable events coexist, portable on SQLite + PostgreSQL.
- 2026-11-17 WP0.2: `props` accept only ints, bools and short codes (start with a letter, no 5+ digit run, ≤8 keys); anything else is dropped silently — the conservative reading of "no PII".
- 2026-11-17 WP0.2: `weekly_active` is emitted from order creation and booking (the core loop), not from every request — GET requests do not commit, so a per-request write would be lost or add load.
- 2026-11-17 WP0.2: funnel window = Asia/Dhaka days `[from, to]` inclusive, max 366 days; counts distinct shops per step. Permission `OPS_READ` (SUPPORT, OPS, SUPERADMIN).
- 2026-11-18 WP0.2: no new env variable — the funnel is a DB feature flag and `ECOMSBD_SENTRY_DSN` already existed in `env/production.example.json` and `Env.sentryDsn`; `.env*.example` unchanged.
- 2026-11-18 WP0.2: `plan_paid` is emitted in `BillingService._record_transaction` for VERIFIED PURCHASE/RENEWAL with amount > 0, keyed by transaction id — the single place every payment lands; manual admin credit is not a payment.
- 2026-11-18 WP0.2: Sentry breadcrumbs: allow-list of categories (navigation, lifecycle, device, connectivity, http) with `message` cleared and only structural data keys kept; console/print/taps/custom dropped. Events: phones masked, message params dropped, user reduced to id, request and screenshots off.
- 2026-11-17 WP0.2: pre-existing, not caused by V4: `tests/test_tenant_isolation.py::TestCommerceCoreIsolation::test_stock_movements_are_not_visible_across_shops` failed when run alone (`NoReferencedTableError` warehouses — import order). Fixed 2026-11-18 (see above).
- 2026-11-17 env: `backend/.venv` points at another Windows user's Python; this machine uses `C:\Users\userw\.venvs\ecomsbd-backend` (uv, `pip install -e .[dev]`). Git runs with `-c safe.directory=E:/ecomsbd` (repo owned by another SID); no global config changed.

## Session log (newest first, one line each)
- 2026-11-18 operator decisions 4f5b82e; WP0.3 slice 1 deterministic + corpus 85463f8; checkpoint (context)
- 2026-11-18 WP0.2 done: emits 0279a22, Flutter Sentry b64ab8d, realtime silent table 58144bd; checkpoint (context)
- 2026-11-17 WP0.2 slice 1: product_events + a40001 + funnel endpoint 390b18b; checkpoint (context)
- 2026-11-17 WP0.1 package id + IMPLEMENTATION_STATUS top + ledger 90edda4
- 2026-11-17 setup: spec + run prompt committed on main (2c2783d, not pushed); v4/phase-0 created
