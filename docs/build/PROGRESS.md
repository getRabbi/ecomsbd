# V4 build ledger
Spec: docs/V4_MASTER_BUILD_SPEC.md · Started: 2026-11-17 · Branch: v4/phase-0

## Now
WP: WP0.7 — Courier live-readiness   Step: 1 (plan in ≤ 10 bullets here, then explore narrowly). Read only spec §2 WP0.7.
Next-session notes: operator rule: targeted tests only (no full suites). WP0.6 provider-independent parts are done; the BD Courier adapter waits for its contract (operator action). The quick-order sheet chip lands with WP1.5 (reuse `courierHistoryText` / `courierHistoryChip`).

## Status
| WP | Status | Commit | Verified | Notes |
|---|---|---|---|---|
| WP0.1 | DONE | 90edda4 | backend 1805 passed/5 skipped, ruff+format+mypy clean; Flutter 541 (perf test flaky under load, green alone); web 66 | docs only: no flag/env/migration needed |
| WP0.2 | DONE | 58144bd | backend 1830 passed/5 skipped (25 new), ruff+format+mypy clean (319 files); Flutter analyze + format clean, 548 tests (7 new) with the pre-existing timing flake below; migration tests green on SQLite | PostgreSQL suite + a40001 round trip run at phase end |
| WP0.3 | DONE | c97712c | backend 1948 passed/5 skipped (118 new across slices), ruff+format+mypy clean (328 files); corpus deterministic 95.8%, merged with recorded answers 100% (authored, optimistic); holdout deterministic-only 50.0% (phones 8/8, address 4/8, items 0/8, names 3/8) | gate G0.3 = merged ≥ 90% on the holdout with the live model — waiting on operator; PostgreSQL suite + a40001 round trip (now with `ai_usage`) at phase end |
| WP0.4 | DONE | aaa77bd | backend 1993 passed/5 skipped (45 new), ruff+format+mypy clean (334 files); Flutter analyze + format clean, all tests green (9 new, perf excluded as in CI); web typecheck + lint clean, 71 tests (5 new) | flag `delivery_rules_enabled` default off; no new env; no runbook (no job/provider/webhook) |
| WP0.5 | DONE | 495a35c | targeted: backend test_documents + test_orders + test_delivery_rules + test_platform_primitives 143 passed, ruff + mypy clean; Flutter order_print_sheet + commerce_screens + delivery_rules + chat_orders_inbox 41 passed, analyze clean on changed files; web typecheck + lint clean, 72 tests | flag `order_documents_enabled` default off; `printing` ^5.15.1 |
| WP0.6 | BLOCKED: EXTERNAL_RISK_CONTRACT_REQUIRED | see log | provider-independent parts done: backend test_courier_history 12 + test_risk_network_v37/automation/migrations 71 passed, ruff + mypy clean; Flutter courier_history_chip + external_risk_card + commerce_screens + risk_and_courier_v1 38 passed, analyze clean; web typecheck + lint, 77 tests | no adapter until the contract exists; quick-order sheet chip → WP1.5 |
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
| G0 | waiting on operator | G0.3: `python -m app.orders.parser.eval --live --set holdout` (merged gated ≥ 90%); offline holdout deterministic-only 50.0% |
| G1 | | |
| G2 | | |
| G3 | | |
| G4 | | |
| G5 | | |

## Operator actions waiting (things only Rabbi can do)
- [ ] WP0.6: supply the BD Courier (bdcourier.com) API documentation (or name another licensed provider and its docs). It is transcribed into `docs/providers/bdcourier/CONTRACT.md`; then the adapter is built on `app/risk_providers/contract.py` (fills `CourierHistory`). Never the seller's courier-panel password.
- [ ] WP0.2 (in progress — DSN being created): create the Sentry project, put its DSN in `apps/mobile_flutter/env/production.json` as `ECOMSBD_SENTRY_DSN`, build, and confirm one test event arrives with phones masked.
- [ ] WP0.2 (in progress — operator turning it on): turn `product_events_enabled` on (admin console, global) when ready to collect the funnel; check `GET /v1/admin/ops/funnel`.
- [ ] WP0.3: add 30–50 real masked seller messages (phones replaced with the 017000000xx range, names/addresses kept as typed) to `backend/tests/fixtures/parse_holdout/holdout.yaml`, then run `python -m app.orders.parser.eval --live --set holdout` with `AI_PARSE_API_KEY` and `AI_PARSE_MODEL` (e.g. `claude-haiku-5-5`) set (costs a few cents; runbook `docs/runbooks/AI_PARSE.md`); G0.3 is met when the merged gated accuracy (phones+address+items) is ≥ 90%.
- [x] Perf test decision (2026-11-18): keep every budget; tag `perf`, run alone in CI with `--concurrency=1`. Done.

## Decisions made during build (spec was silent)
- 2026-11-18 WP0.6: courier history is a new optional `CourierHistory` on the existing `ProviderResult` (port unchanged for current adapters); courier keys are normalised slugs (any provider-reported courier, max 12 rows) rather than a fixed list, since the provider's courier set is unknown until its contract exists. A row with delivered + cancelled > total or out-of-range counts is dropped, not repaired. The provider's own aggregate is used when valid, else rows are summed.
- 2026-11-18 WP0.6: under 3 parcels the history state is `INSUFFICIENT_DATA` and `success_bps` is null; the chip then says "too little data" ("কম ডেটা") instead of any count, and `external_success_bps` stays unset so no workflow condition matches thin data.
- 2026-11-18 operator: do not re-run the full backend / Flutter suites (credit cost); run only the tests the change touches, plus ruff/format/mypy, `flutter analyze` on changed files and the web checks. Overrides §1.5 item 2 / §1.6 loop step 4 "full suite once per WP end".
- 2026-11-18 WP0.5: web prints through a hidden iframe (`srcdoc`, sandbox `allow-modals allow-same-origin`, no scripts) instead of a new window, so no pop-up blocker is involved; `fetchHtml` goes through the existing `request` (new `responseType: 'html'`) so every API call still uses the one authenticated `fetch(buildUrl(...))` path the parity test guards.
- 2026-11-18 WP0.5: mobile wraps `printing` behind `DocumentPrinter` (`documentPrinterProvider`), so widget tests stub the OS print sheet; `ApiClient.getText/postText` fetch HTML as plain text and still translate JSON error bodies. The print action is hidden for an order that exists only on the phone (not synced, no server id to render).
- 2026-11-18 WP0.5: "mask on label" is a per-print choice (`mask_phone` query/body flag, default false = full phone), not a stored setting; clients may remember it locally.
- 2026-11-18 WP0.5: Jinja2 is not a dependency, so documents are string templates with `html.escape` on every field. Noto Sans Bengali 400/700 copied from the Flutter assets into `app/documents/fonts/` with an OFL.txt (copyright read from the font's name table); embedded once per HTML.
- 2026-11-18 WP0.5: Code128-B test vectors are hand-derived from the ISO/IEC 15417 value table and backed by table invariants (107 unique symbols, 11 modules, even bar sums, stop 13), not an external sample file.
- 2026-11-18 WP0.5: a print writes one audit row `privacy.documents_printed` (kind, size, count, full/masked) per request instead of one phone reveal per customer; no phone in the row.
- 2026-11-18 WP0.4: the zone resolver reuses the parser's 64-district gazetteer (`app/orders/parser/districts.py`, EN + BN) and adds `app/orders/data/zones.yaml` (Dhaka city areas, Dhaka suburb areas; package-data). Dhaka, Gazipur and Narayanganj hold both city and suburbs, so without a listed area they resolve to `null`; any other district is `OUTSIDE_DHAKA` and beats a Dhaka area name ("Mirpur, Kushtia"). A listed city area alone ("Uttara") is `INSIDE_DHAKA`.
- 2026-11-18 WP0.4: zone charges default to 0 = not set, so nothing is added to a COD until the seller enters charges; GET returns the defaults with `configured: false` and never writes a row. Flag `delivery_rules_enabled` (default off) gates both the API (`FEATURE_DISABLED`, 403) and the chat-draft COD step.
- 2026-11-18 WP0.4: chat drafts mark a mismatch with the existing uncertain key `amount` (the clients already highlight it) rather than a new `cod_amount` key, plus warning `COD_MISMATCH`; an address whose zone is unknown adds no charge and warns `ZONE_UNKNOWN`. A stated amount is never replaced. Drafts carry no discount, so the draft calculation uses 0; `calculate_cod` takes a discount for order-side callers.
- 2026-11-18 WP0.4: `chat_order_drafts.cod_source` widened 12 → 16 in a40001 for `CHAT_MISMATCH` (batch_alter_table; downgrade narrows back).
- 2026-11-18 WP0.4: `shop_delivery_rules` is announced on the realtime `shop` topic (like `network_preferences`); PATCH writes audit action `delivery_rules.updated` with only the changed field names. `default_courier` must be a known courier provider key (`steadfast`, `pathao`, `redx`).
- 2026-11-18 WP0.4: the web form helpers (`lib/delivery-rules.ts`) are self-contained because `npm test` runs files with Node's type stripping, which cannot follow extensionless value imports; `tests/delivery.test.mjs` checks their taka↔paisa conversion matches `lib/orders.ts` exactly.
- 2026-11-18 WP0.3: the Anthropic client uses JSON-schema structured output (`output_config.format`) with **no tools** instead of a forced tool call — same schema guarantee, nothing the model can act with, and current models (Opus 5.5, Sonnet 5.5) return 400 on forced `tool_choice`. `anthropic-version: 2026-10-21`, no `temperature`, `effort: low`. Example model `claude-haiku-5-5` ($0.10 / $0.50 per MTok, estimate in env examples).
- 2026-11-18 WP0.3: text longer than `AI_PARSE_MAX_INPUT_CHARS` is not sent (deterministic + `AI_PARSE_UNAVAILABLE`) rather than truncated — a cut paste can lose the order.
- 2026-11-18 WP0.3: one `ai_parse_monthly` unit is spent before the call (bounds cost under concurrency); a failed call still spends it and is recorded as `ai_usage` outcome `error`. Cache hits spend nothing.
- 2026-11-18 WP0.3: `ai_usage` adds `provider` and `error_code` (machine code only) to the spec columns; `cost_micro_usd` is NULL when prices are unset (unknown ≠ free). Prices are `Decimal` settings.
- 2026-11-18 WP0.3: a model value's confidence is its own `field_confidence` capped at 0.75 (default 0.5), so it never reads as labelled/exact. `uncertain_fields` uses the confidence keys (`name`, `address`, `items`, `amount`). The warning is the literal code `AI_PARSE_UNAVAILABLE` in `warnings`.
- 2026-11-18 WP0.3: last-address prefill runs only with `ai_parse_enabled` on (it is part of the flagged feature), reads the newest non-deleted order's `delivery_address_raw` for the customer found by phone HMAC, and works whether or not the model answered.
- 2026-11-18 WP0.3: holdout phones masked to the 017000000xx test range (separators and digit script kept) — §1.6 forbids real numbers in tests; nothing else in the operator's 8 messages changed. Holdout ids `h-01`..`h-08`.
- 2026-11-18 WP0.3: the eval grader compares items as (word set of name+colour+size, quantity), ignoring the word size/সাইজ, so "black hoodie" + size XXL equals "black hoodie XXL" and "শাড়ি" + colour নীল equals "শাড়ি (নীল)". Grading only — the parser is unchanged; corpus deterministic score unchanged at 95.8%.
- 2026-11-18 WP0.3: chat-order drafts (`app/chat_orders/service.py`) still parse deterministically; the enhanced path is wired to `POST /v1/orders/parse` only. Drafts move to the Intake service in WP1.3 (L1).
- 2026-11-18 operator correction (WP0.3): on 8 held-out messages the deterministic parser got phones 8/8, names ~1/8, items ~1/8 while `is_low_confidence` was False for 7/8; the 50-case corpus was written and tuned by the agent, so 95.8% is optimistic. Now: deterministic = phones + labelled fields only (no more tuning); LLM is called unless name/address/items/cod all ≥ 0.8; merge keeps det ≥ 0.8 else LLM, disagreement on a labelled field → `uncertain_fields`; empty address + known customer → last delivery address (uncertain); holdout set `parse_holdout/holdout.yaml` never tuned on; G0.3 measured on the holdout with the live LLM (operator). Spec WP0.3 section updated.
- 2026-11-18 WP0.3: so that confidence ≥ 0.8 means "came from a label", the two unlabelled heuristics that reported HIGH (address with ≥ 2 place hits, currency-marked amount) now report MEDIUM. Extraction is unchanged.
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
- 2026-11-18 WP0.6 provider-independent courier history 0ce0cfa + clients; BLOCKED: EXTERNAL_RISK_CONTRACT_REQUIRED
- 2026-11-18 WP0.5 DONE (targeted tests per operator rule) 495a35c
- 2026-11-18 WP0.5 web + mobile printing committed; full suites stopped by the machine for low memory — re-run pending
- 2026-11-18 WP0.5 slice 1 (backend documents) d99556b; stopped at usage limit — web/mobile print next
- 2026-11-18 WP0.4 delivery rules + zones + COD (backend, mobile, web) aaa77bd
- 2026-11-18 operator correction to WP0.3 recorded ef3dc03; WP0.3 slice 2 (LLM path, holdout, eval) c97712c — WP0.3 DONE, G0.3 waiting on operator; checkpoint (context)
- 2026-11-18 operator decisions 4f5b82e; WP0.3 slice 1 deterministic + corpus 85463f8; checkpoint (context)
- 2026-11-18 WP0.2 done: emits 0279a22, Flutter Sentry b64ab8d, realtime silent table 58144bd; checkpoint (context)
- 2026-11-17 WP0.2 slice 1: product_events + a40001 + funnel endpoint 390b18b; checkpoint (context)
- 2026-11-17 WP0.1 package id + IMPLEMENTATION_STATUS top + ledger 90edda4
- 2026-11-17 setup: spec + run prompt committed on main (2c2783d, not pushed); v4/phase-0 created
