# V4 release (Phases 0–7) — pull request notes

Branch chain: `v4/phase-0` → `v4/phase-1` → `v4/phase-2` → `v4/phase-3` → `v4/phase-4` → `v4/phase-5`
→ `v4/phase-6` → `v4/phase-7` (each cut from the one before; none merged to `main`). This PR is the newest branch → `main`.
Spec: `docs/V4_MASTER_BUILD_SPEC.md`. Ledger (per-WP commits, tests, decisions):
`docs/build/PROGRESS.md`.

**Safe to merge dark.** Every new feature is behind a flag that defaults **off**, every
new provider is disabled until its contract and credentials exist, and the migrations
only add tables/columns (no data is rewritten except the `chat_order_drafts.capture_source`
backfill to `META_WEBHOOK`). With every flag off, the app behaves as V3.

## What Phases 0–7 add

**Phase 0 — make the core loop real**
- WP0.1 docs truth. WP0.2 crash + funnel visibility: `product_events` + admin funnel
  (`GET /v1/admin/ops/funnel`), Sentry with phone masking (mobile DSN via `env/production.json`).
- WP0.3 LLM order parser (`EnhancedOrderParser`): deterministic first, the model only for
  guessed/missing fields, strict schema (L12), `ai_usage` cost rows, 24 h answer cache,
  metered by `ai_parse_monthly`; eval harness `python -m app.orders.parser.eval`.
- WP0.4 shop delivery rules + COD auto-calculation (zone charges, default courier, thresholds).
- WP0.5 invoice + 4×6 shipping label (web + mobile print).
- WP0.6 courier-history chip + external-risk card (provider adapter **BLOCKED:
  EXTERNAL_RISK_CONTRACT_REQUIRED**).
- WP0.7 courier live-readiness: `app.provider_smoke` (read-only by default), Pathao
  location resolver, statement profile loader (**BLOCKED: STATEMENT_SAMPLE_REQUIRED**).

**Phase 1 — intake (auto-detect)**
- WP1.1 linked devices: `DEVICE` connections, scoped ingest keys (`intake.capture`), pairing codes.
- WP1.2 versioned intake rules (`intake_rule_sets`, admin publish / activate / rollback, ETag).
- WP1.3 capture endpoint + Intake service (`POST /v1/intake/captures`, manual share/paste,
  dedupe, 24 h duplicate-order link, retention).
- WP1.4 Android Notification Listener (native Kotlin, heads-up + translucent quick-order;
  no overlay / accessibility / call-log / SMS permissions).
- WP1.5 quick-order sheet + Android share target (iOS Share Extension = operator, needs Xcode).

**Phase 2 — confirmation engine + manual payments**
- WP2.1 `order_confirmations` (own state machine, attempts, idempotent requests).
- WP2.2 deterministic, explainable call policy (stored reason code; no ML).
- WP2.3 manual call queue (quiet hours, audited number reveal, rate limit) + SMS confirm
  link (`/c/<token>`; gateway **BLOCKED: SMS_PROVIDER_CONTRACT_REQUIRED**). A seller's
  "Dhoreni" also sends the link once (`SELLER_REVIEW → SMS_LINK_SENT`, `SELLER_NO_ANSWER`).
- WP2.4 automation hooks: confirmation + intake triggers, `REQUEST_CONFIRMATION`,
  `CONVERT_DRAFT_TO_ORDER`, recipes incl. "Customer confirmed → book courier".
- WP2.5 seller plan payment by bKash/Nagad TrxID (admin approve/reject; never in store builds).

**Phase 3 — voice IVR** (mock provider only; real calls **BLOCKED:
VOICE_PROVIDER_CONTRACT_REQUIRED**)
- WP3.1 `VoiceTransport` port + YAML capability manifests + platform voice account.
- WP3.2 call orchestration: dispatcher, signed callbacks, DTMF 1/2/3, reconciler, per-customer cap.
- WP3.3 shop intro audio versions (operator upload; TTS **BLOCKED: TTS_PROVIDER_REQUIRED**),
  seller listen + turn on (web).
- WP3.4 call credits: plan allowance → packs, `VOICE_DAILY_GLOBAL_CAP` (every mode),
  `VOICE_DAILY_SHOP_CAP` (Free Launch), admin grant + report.

**Phase 4 — Autopilot** (opt-in, L11)
- Shop gate (Settings checklist, re-checked every run) + order gate in
  `CONVERT_DRAFT_TO_ORDER`; any failure leaves the draft with the seller and a reason code.
  Autopilot never books: booking stays the WP2.4 confirmed → book workflow.
- Pathao orders need resolved city/zone; RedX orders are always held (`LOCATION_UNRESOLVED`).
- Autopilot shops' automatic uploads are read through the LLM merge path (metered).
- Recipe "Autopilot" installed disabled; kill switches: flag, per-shop toggle, Pause 1 hour.
- Web + mobile: Settings → Autopilot, Home "Autopilot today".

**Phase 5 — Chrome extension** (`apps/extension`, MV3, TypeScript, plain `tsc`)
- Pairs with the WP1.1 6-digit code (web Settings → Browser extension); capture-only key in
  `chrome.storage.local`; Unlink revokes it (`POST /v1/intake/unlink`).
- Content script on `business.facebook.com`, `www.messenger.com`, `web.whatsapp.com` only; reads the
  open conversation only after a Capture tap, with verified selectors from `/v1/intake/rules` (none
  ship: the seed has none verified, so it captures the selection with the spec notice); phone
  highlight in the open chat via the CSS Highlight API.
- `POST /v1/intake/captures/extension` → an `EXTENSION` draft; the answer carries ids and field
  names only. The side panel opens web `/quick-order?draft=<id>` (seller session, order composer).
- CI job `extension` (typecheck, unit tests, build); store listing + privacy disclosure EN + BN in
  `apps/extension/STORE_LISTING.md`. No migration.

**Phase 6 — official Meta path on** (no migration)
- A delivering Messenger connection (a Page) covers Meta Business Suite and a delivering WhatsApp
  Cloud API number covers WhatsApp Business in `shop_intake_settings.covered_packages`; phones drop
  covered apps on the next rules refresh, so a Page order arrives once (webhook), not twice.
  Personal Messenger / WhatsApp are never covered. Recomputed on every connection change, health
  check and hourly.
- Messenger / WhatsApp leave "Coming soon" (web + mobile hub) only with
  `META_APP_REVIEW_APPROVED=true`, the Meta app configured, the shop's `meta_official_path_enabled`
  and a passing health check. Runbook `META_OFFICIAL_PATH.md`; G6 = Meta App Review.

**Phase 7 — more reach**
- WP7.3 own API domain: every URL from `PUBLIC_BASE_URL`; `PUBLIC_BASE_URL_ALIASES` keeps earlier
  origins served during a move (their hosts join `TRUSTED_HOSTS`); `python -m app.core.public_urls`
  prints every externally registered URL; Codemagic guard accepts both production API origins.
  Runbook `API_DOMAIN_MIGRATION.md` (the move itself is an operator step).
- WP7.4 referrals: one code per shop; another shop enters it before its first payment; when that
  shop's first TrxID payment is approved, both get 30 days of plan through the admin-manual grant,
  once (outbox `referral.payment_approved`, unique billing keys). Held for support when the
  referrer pays through Play / bKash web. Web Plans card; runbook `REFERRALS.md`.
- WP7.1 SMS gateway (**BLOCKED: SMS_PROVIDER_CONTRACT_REQUIRED**) and WP7.2 Carrybee / Paperfly
  (**BLOCKED: CARRYBEE_CONTRACT_REQUIRED, PAPERFLY_CONTRACT_REQUIRED**): nothing built.

## Migrations (single head `a47001`, all portable, downgrade implemented)

| Id | File | Adds |
|---|---|---|
| `a40001` | `20261117_0100_a40001_v4_phase0.py` | `product_events`, `ai_usage`, `shop_delivery_rules`; widens `chat_order_drafts.cod_source` (16 chars, `CHAT_MISMATCH`); adds `external_risk_lookups.courier_history` |
| `a41001` | `20261118_0100_a41001_v4_phase1.py` | `device_ingest_keys`, `device_pairing_codes`, `intake_rule_sets` (seed v1), `shop_intake_settings`; `chat_order_drafts.capture_source` (backfill `META_WEBHOOK`) + `duplicate_of_order_id` |
| `a42001` | `20261119_0100_a42001_v4_phase2.py` | `order_confirmations`, `confirmation_attempts` (+ `segments`), `confirmation_links`, `billing_payment_submissions` |
| `a43001` | `20261119_0100_a43001_v4_phase3.py` | `voice_provider_connections`, `voice_callback_deliveries`, `voice_call_events` (platform tables), `voice_intro_audio`, `voice_credit_packs`, `voice_call_charges`, `voice_daily_usage` |
| `a44001` | `20261120_0100_a44001_v4_phase4.py` | `shop_delivery_rules`: `autopilot_on`, `autopilot_consent_at/_by`, `autopilot_paused_until`, `autopilot_manual_queue`; `autopilot_decisions` |
| `a47001` | `20261121_0100_a47001_v4_phase7.py` | `referral_codes`, `referrals` (phases 5 and 6 add no migration) |

Every new business table is tenant-owned with RLS on and client-role grants revoked
(pattern of `a39001`); platform tables (voice account, callbacks, daily usage) have RLS on
and client roles revoked. Integration checkpoint and end-run results (PostgreSQL suite +
round trips): see the last two sections.

## New feature flags (all default **off**, `app/common/feature_flags.py`)

| Flag | Turns on | Needs first |
|---|---|---|
| `product_events_enabled` | funnel events + admin funnel | — |
| `delivery_rules_enabled` | zone charges, COD auto-calc, default courier | — |
| `order_documents_enabled` | invoice + shipping label | — |
| `intake_device_enabled` | linked phones, captures, intake rules | — |
| `confirmation_engine_enabled` | customer confirmations, policy, call queue | `delivery_rules_enabled` (thresholds) |
| `confirmation_sms_link_enabled` | SMS confirm link | engine + `SMS_TRANSPORT` + `PUBLIC_WEB_URL` (gateway contract) |
| `manual_payments_enabled` | bKash/Nagad TrxID plan payments | `MANUAL_PAYMENT_ACCOUNTS` |
| `voice_confirmation_enabled` | IVR calls, intro audio | engine + `VOICE_TRANSPORT` + provider account + intro + credit |
| `autopilot_enabled` | Autopilot (global kill switch) | the whole checklist per shop |
| `extension_enabled` | Chrome extension pairing + every extension ingest call | `intake_device_enabled` |
| `meta_official_path_enabled` | Meta coverage of the phone's business apps + Messenger / WhatsApp out of Coming soon | `META_APP_REVIEW_APPROVED` + a passing health check |
| `referrals_enabled` | referral codes + 30-day credit for both shops | `manual_payments_enabled` (the credit follows a TrxID payment) |

Existing flag now used by V4: `ai_parse_enabled` (the LLM parser; off). New entitlements:
`voice_calls_monthly` (Free 0 / Starter 50 / Pro 200), `autopilot` (Pro).

## New environment variables (both `.env.example` and `.env.production.example`)

| Variable | Default | When |
|---|---|---|
| `AI_PARSE_PROVIDER` | `anthropic` | with `ai_parse_enabled` |
| `AI_PARSE_API_KEY` | empty (secret) | REQUIRED WHEN `ai_parse_enabled` |
| `AI_PARSE_MODEL` | `claude-haiku-5-5` | REQUIRED WHEN `ai_parse_enabled` |
| `AI_PARSE_TIMEOUT_SECONDS` | `8` | optional |
| `AI_PARSE_MAX_INPUT_CHARS` | `6000` | optional |
| `AI_PARSE_PRICE_INPUT_PER_MTOK_USD` / `_OUTPUT_` | `0.10` / `0.50` | cost estimate in `ai_usage` |
| `MANUAL_PAYMENT_ACCOUNTS` | empty | REQUIRED WHEN `manual_payments_enabled` (JSON list) |
| `VOICE_TRANSPORT` | `disabled` | REQUIRED WHEN `voice_confirmation_enabled` (`mock` refused when deployed) |
| `TTS_PROVIDER` | `disabled` | optional (`fake` refused when deployed) |
| `VOICE_AMOUNT_PREFIX_AUDIO_REF` / `_SUFFIX_` | empty | optional, both or neither (spoken COD) |
| `VOICE_DAILY_GLOBAL_CAP` | `300` | every mode; 0 = no calls |
| `VOICE_DAILY_SHOP_CAP` | `30` | Free Launch Mode only; 0 = no calls |
| `META_APP_REVIEW_APPROVED` | `false` | set `true` only after Meta App Review approval (WP6) |
| `PUBLIC_BASE_URL_ALIASES` | empty | during an API domain move (WP7.3); https in production |

Existing variables V4 reads: `SMS_TRANSPORT`, `PUBLIC_WEB_URL`, `R2_*` (intro audio),
`FREE_LAUNCH_MODE`, `DISTRIBUTION_CHANNEL`. Mobile build defines: `ECOMSBD_SENTRY_DSN`
(`env/production.json`), `ECOMSBD_DIRECT_BUILD` (not used: no direct APK, operator decision).
Extension build (not server env): `ECOMSBD_API_BASE_URL` (default `https://api.scalemyprints.com`),
`ECOMSBD_WEB_BASE_URL` (default `https://app.ecomsbd.com`); https origins only.

## New jobs, topics, routes (runbooks in `docs/runbooks/`)

- Worker: `follow_up_confirmations` (SMS link / retries / expiry), `dispatch_confirmation_calls`
  (IVR + reconciler). Runbooks `ORDER_CONFIRMATIONS.md`, `VOICE_CONFIRMATION.md`.
- Outbox topics (each with a handler): `intake.draft_ready`, `confirmation.*`,
  `confirmation.link_requested`, `billing.manual_payment_reviewed`, `voice.intro_requested`,
  `referral.payment_approved`.
- Referrals: `GET /v1/referrals`, `POST /v1/referrals/code`, `POST /v1/referrals/apply`; admin
  `GET /v1/admin/referrals`. Operator CLI: `python -m app.core.public_urls`.
- Extension (ingest key): `POST /v1/intake/captures/extension`, `POST /v1/intake/unlink`; seller:
  `GET /v1/devices/extensions`; web `/settings/extension`, `/quick-order?draft=<id>`.
- Public: `GET/POST /v1/public/confirmations/{token}` (+ web `/c/[token]`),
  `POST /v1/webhooks/voice/{provider}` (refuses everything without a verified provider).
- Runbooks: `AI_PARSE.md`, `INTAKE_RULES.md`, `INTAKE_CAPTURE.md`, `ORDER_CONFIRMATIONS.md`,
  `MANUAL_PAYMENTS.md`, `VOICE_CONFIRMATION.md`, `AUTOPILOT.md`, `EXTENSION.md`,
  `META_OFFICIAL_PATH.md`, `API_DOMAIN_MIGRATION.md`, `REFERRALS.md`.

## Production deploy order (after the merge)

1. `alembic upgrade head` on the production database **first** (→ `a47001`). The API image
   runs no migration on boot (`infra/northflank/README.md`), and the new models read columns
   the old schema lacks (e.g. `chat_order_drafts.capture_source`). Every column V4 adds to an
   existing table is nullable or has a server default, so the running V3 API keeps working
   on the migrated schema.
2. Then let Northflank roll out `main` (API + worker).
3. Web: `cd apps/web && npm run cf:deploy` from a clean checkout of `main`. Nothing deploys
   the Cloudflare Worker (`ecomsbd.com`, `app.ecomsbd.com`) automatically
   (`apps/web/CLOUDFLARE.md`). The failing "Vercel" status on this PR is the disabled old
   Vercel project; it deploys nothing and blocks nothing.
4. Mobile: the next build's privacy policy and account deletion links open `ecomsbd.com`
   (they opened `scalemyprints.com`, which is down).

## Staging turn-on order

Each step is a flag on for one test shop first (admin console, tenant row), checked, then
widened. Stop at the first step whose check fails; turning a flag off is the rollback.

1. Deploy; `alembic upgrade head` (→ `a47001`, one head). Nothing visible changes.
2. `product_events_enabled` (global) → `GET /v1/admin/ops/funnel` shows events. Ship the
   mobile build with `ECOMSBD_SENTRY_DSN`; one test event arrives with the phone masked.
3. `delivery_rules_enabled` → set three zone charges + default courier; a new order's COD
   = items + zone charge. Then `order_documents_enabled` → print an invoice and a label.
4. `ai_parse_enabled` with `AI_PARSE_API_KEY` / `AI_PARSE_MODEL` — only after the G0.3
   live holdout run ≥ 90% (runbook `AI_PARSE.md`).
5. Courier live: run `python -m app.provider_smoke steadfast --account-id <id>`, then add
   `live_verification` to the courier's manifest (runbook `AUTOPILOT.md`).
6. `intake_device_enabled` → link an Android phone, publish/activate rules, capture a
   WhatsApp message into Chat orders (G1 steps in the ledger).
7. `confirmation_engine_enabled` → a new order gets a confirmation and appears in the call
   queue. Then, once an SMS gateway exists: `SMS_TRANSPORT`, `PUBLIC_WEB_URL`,
   `confirmation_sms_link_enabled`. Separately: `MANUAL_PAYMENT_ACCOUNTS` +
   `manual_payments_enabled` → one staged TrxID payment approved (G2).
8. Voice, once a provider contract exists: `VOICE_TRANSPORT`, the provider account
   (`PUT /v1/admin/voice/providers/{provider}`), an intro per shop, credit, then
   `voice_confirmation_enabled` (G3: 200 real calls, report).
9. `autopilot_enabled` for pilot shops only (Pro, ≥ 20 confirmed, default courier
   live-verified, consent in Settings → Autopilot, the "Autopilot" and "Customer confirmed
   → book courier" workflows on). G4: 5 shops × 14 days, 0 wrong bookings.
10. `extension_enabled` (with `intake_device_enabled`) → build `apps/extension` for staging, load it
    unpacked, pair from Settings → Browser extension, capture a selected test message, finish the
    order on `/quick-order` (runbook `EXTENSION.md` → Verification). Page selectors: verify per host,
    then publish a rule set with `verified_at`. G5: Chrome Web Store approval.
11. After Meta App Review: `META_APP_REVIEW_APPROVED=true`, `meta_official_path_enabled` for the test
    shop, connect the test Page, one Page order end to end (runbook `META_OFFICIAL_PATH.md`). G6.
12. `referrals_enabled` (needs step 7's TrxID payments) → two test shops, one approved payment, both
    plans +30 days (runbook `REFERRALS.md` → Verification).
13. API domain move, when wanted: runbook `API_DOMAIN_MIGRATION.md` (`PUBLIC_BASE_URL_ALIASES`).

## Blocked / operator items (not in this PR)

`EXTERNAL_RISK_CONTRACT_REQUIRED` (WP0.6), `STATEMENT_SAMPLE_REQUIRED` (WP0.7),
`SMS_PROVIDER_CONTRACT_REQUIRED` (WP2.3), `VOICE_PROVIDER_CONTRACT_REQUIRED` (WP3.1),
`TTS_PROVIDER_REQUIRED` (WP3.3), `SMS_PROVIDER_CONTRACT_REQUIRED` (WP7.1),
`CARRYBEE_CONTRACT_REQUIRED` + `PAPERFLY_CONTRACT_REQUIRED` (WP7.2); iOS Share Extension (Xcode); extension page selectors (operator
verifies on the live pages) and the Chrome Web Store submission (G5); Meta App Review (G6); the API
domain move (WP7.3); gates G0–G6 need real-world
evidence (ledger → "Operator actions waiting").

## Integration checkpoint (2026-11-20)

Local `docker compose up -d` (Docker 29.9.3; `postgres:16-alpine`, `redis:7-alpine`),
against the compose database with an explicit local `DATABASE_URL`:

- `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`:
  **green** — `a44001 → a43001 → a44001`, `alembic heads` = one head `a44001`.
- Extra, because no Phase 0–3 round trip had run on PostgreSQL: `alembic downgrade a39001`
  (a44001 → a43001 → a42001 → a41001 → a40001 → a39001) then `upgrade head`: **green**.
- PostgreSQL suite (`TEST_DATABASE_URL=…/ecomsbd_test python -m pytest -q`): **not run to
  the end** — the session stopped it because this 8 GB machine ran low on memory (not a
  test failure). Its session setup did migrate `ecomsbd_test` to `a44001` through the
  suite's own Alembic path before it stopped. No test result exists yet; run it on a
  machine with more memory or in CI before merging (operator action in the ledger).

## Test runs after the checkpoint (2026-11-21)

- Operator, elsewhere, PostgreSQL suite on `v4/phase-4` @ 395e891: 2439 passed, 3 failed, 1 skipped.
  The 3 failures were fixed on `v4/phase-5` (realtime table coverage for `autopilot_decisions` /
  `billing_payment_submissions` / the voice tables; `migrations/env.py` keeps existing loggers;
  the dispatcher test drains) and their tests are green.
- Every WP since was verified with targeted suites (ledger Status table: WP5, WP6, WP7.3, WP7.4).
- Operator, elsewhere, end run on `v4/phase-7` @ 43b1090:
  - SQLite: "2 failed, 2484 passed, 6 skipped in 1115.27s"
  - PostgreSQL 16: "2 failed, 2489 passed, 1 skipped in 1162.50s"
  - PostgreSQL migrations: single head `a47001`; base → head, head → `a39001` → head,
    `-1` → head: OK.
- The same 2 tests failed on both databases; both are test-side and fixed with no app change
  (the two tests pass):
  - `test_integrations_shopify.py::test_shopify_is_gated_until_the_platform_app_exists`: the
    expected SHOPIFY hub entry now includes `"coming_soon": True` (field added by WP6, 60d8a0e).
  - `test_rto.py::TestIsolationAndPrivacy::test_the_phone_never_reaches_the_logs`: the only record
    with the phone was the test client's own `httpx` request line
    (`HTTP Request: GET http://testserver/v1/customers/risk-check?phone=… 200 OK`), visible since
    `migrations/env.py` stopped disabling existing loggers (F2). `tests/conftest.py` sets the
    `httpx` / `httpcore` loggers to WARNING; production lines go through the redacting
    `JsonFormatter`. The test is unchanged.

## Phase 8 (runs 5–10, branch `v4/phase-8`, 2026-11-23)

Branched from `v4/phase-7`; merge after (or with) PR #42.

**Adds**
- WP7.1 Alpha SMS (`sms.net.bd`) behind the existing `SmsTransport` port, used by the WP2.3
  confirm link. Contract `docs/providers/alpha_sms/CONTRACT.md` (the provider's own API page).
  POST form fields (key never in a URL), every documented error code mapped; a send that may
  have reached the provider is the new `DeliveryOutcome.UNKNOWN` and is never re-sent (the API
  has no idempotency key). Credentials only through the admin console
  (`GET/PUT /v1/admin/sms/providers`, vault-sealed, audited `admin.sms_provider_updated`).
  **Run 6 — tracking SMS:** order updates (the recipes' tracking step, any `SEND_TEMPLATE`
  step, a manual send) can go by SMS through the same gateway. Messaging channel `SMS` is
  available only while the gateway sends (flag + console account); each shop switches its own
  SMS channel on (`messaging_channels`, default off; no migration). SMS contacts = a BD mobile
  or the customer's saved phone; SMS templates = order updates only, no subject, ≤ 3 parts per
  language. A lost answer or a crash mid-send is `UNKNOWN`, never resent; only a provable
  non-send (no connection, 417) is retried. Web: Customer messaging contact + template forms,
  recipe channel, workflow step channel; Flutter: contact form Email / SMS.
- WP7.2 Carrybee courier (`app/couriers/carrybee/`), from Carrybee's own developer reference
  (`docs/providers/carrybee/CONTRACT.md`): credentials check (store list), pickup stores,
  single create sent once (ambiguous -> `BOOKING_UNKNOWN`), status lookup (raw
  `transfer_status`, no state claim: values unpublished), cancel, shared-secret webhook
  (`/v1/webhooks/couriers/carrybee/{token}`, 202 + header echo after verification). Three
  credentials fit the two sealed slots (Client-Secret + Client-Context packed together).
  **Run 6 — display check:** connect / store / webhook were already server-driven; fixed the
  hard-coded names (Carrybee, not `carrybee`, on web orders / money / returns / reconciliation /
  insights, mobile accounts / order courier card / receivables / RTO, and the printed invoice /
  label), the default-courier pickers (web + mobile offer Carrybee when the server reports it on
  for the shop; a saved value always shows) and the web workflow BOOK_COURIER picker (connected
  couriers).
- **Run 9 — WP0.6 BD Courier** external risk lookup (`app/risk_providers/bdcourier.py`) on the
  existing port, from bdcourier.com/api-docs (`docs/providers/bdcourier/CONTRACT.md`; the page is
  client-rendered, its text came from the site's own bundle, hash recorded). Each shop connects
  its own BD Courier key (the provider's terms forbid one account serving many clients; there is
  no platform key). `POST /courier-check` per-courier parcel counts feed the courier-history chip,
  `TOTAL_PARCELS` / `DELIVERED_PARCELS` and `external_success_bps`; BD Courier's own verdict,
  ratio and reports are dropped. Terms built in: a lookup only for a customer with a DRAFT /
  CONFIRMED order from the last 3 days (`NEW_ORDER_REQUIRED`, nothing sent), only on a seller's
  tap, cache ≤ 72 h, answers pruned after 30 days. The flag off hides it everywhere (catalog,
  answers, workflows, confirmation policy). Web Settings → Risk provider shows the terms note.
- CI restore drill runs pg_dump / pg_restore / psql from `postgres:16-alpine` (on `v4/phase-7`).

**Migration** `a48001` (`20261123_0100_a48001_v4_phase8.py`, down_revision `a47001`):
`sms_provider_connections` (platform table, RLS on, client roles revoked). Single head `a48001`.

**Flags (default off):** `alpha_sms_enabled`, `carrybee_enabled`, `bdcourier_enabled` (run 9).
**Environment variables:** none new (Carrybee hosts come from its reference; the SMS key only
from the admin console).
**Runbooks:** `SMS_ALPHA.md`, `COURIER_CARRYBEE.md`, `EXTERNAL_RISK_BDCOURIER.md`.
**Before `bdcourier_enabled` goes on for real shops (operator):** BD Courier's written OK for
sellers using their own keys inside ecomsbd, and the privacy policy / Play Data safety text.

**Still blocked / deferred:** WP0.7 statement profiles (no fixtures),
voice / TTS / Paperfly (deferred by the operator). Carrybee live create and Alpha SMS live send
are operator checks (runbooks).

**CI on PR #43 (`v4/phase-8` @ 5cd3934, run 10, 2026-11-23):** green, including run 9's BD Courier
code. Backend SQLite "2607 passed, 6 skipped in 963.27s"; PostgreSQL 16 "2612 passed, 1 skipped in
1171.07s"; migration sanity from an empty PostgreSQL to `a48001`; lint, format, mypy, SDK checks,
dependency scan; Flutter, backup restore drill, backend image, Chrome extension, secret scan. Run 10
changed CI config only: the secret scan flagged BD Courier's published `Bearer YOUR_API_KEY` sample in
the contract (allowlisted as that literal in that file), and every path-scoped `.gitleaks.toml` entry
now needs path AND value (the default "OR" let a path silence a whole file). Vercel: "Account is
blocked".

**Earlier CI on PR #43 (`v4/phase-8` @ 4cd0a5e, 2026-11-23):** green. Backend SQLite "2566 passed,
6 skipped in 748.71s"; PostgreSQL 16 "2571 passed, 1 skipped in 905.53s"; migrations on an empty
PostgreSQL base → head (`a48001`) → base; lint, format, mypy, SDK checks, dependency scan; Flutter,
backup restore drill, backend image, Chrome extension, secret scan. Two fixes on the way, no
behaviour change: whole-tree ruff (`app/models.py` import order) and a stale `test_insights`
expectation (Carrybee not-live). The Vercel check fails with "Account is blocked" (account, not
code). The commands below remain for a local run.

**Tests run in run 9 (one targeted pass at the end):** backend `test_risk_provider_bdcourier` 41
(new) + `test_external_risk`, `test_risk_network_v37`, `test_courier_history`,
`test_confirmation_policy` = 117 passed; ruff + format on changed files, mypy clean (403 files);
web typecheck + lint, 114 tests (3 new `bdcourier.test.mjs`); Flutter external_risk_card + courier_history_chip 12 passed (2 new: skipped lookup says NEW_ORDER_REQUIRED, EN + BN at 360×800), analyze + dart format clean on changed files.

**Tests run in run 6 (targeted only):** backend `test_messaging_sms` 10 (new) + `test_messaging`,
`test_campaigns`, `test_automation`, `test_automation_workflows`, `test_sms_alpha`,
`test_automation_confirmation_hooks` = 106, `test_documents` 23 (1 new: every manifest courier
prints by name); web typecheck + lint, 111 tests (3 new `messaging-sms.test.mjs`, Carrybee cases
in `ops.test.mjs`); Flutter `messaging_screen_test` 5 (3 new), `delivery_rules_screen_test` 11
(2 new), courier / RTO / insights / courier-history suites 47; ruff + format + mypy and
`flutter analyze` + `dart format` on changed files.

**Tests run in run 5 (targeted only, operator rule):** `test_sms_alpha` 40,
`test_confirmation_links` + `test_transports` + `test_realtime` 74, `test_migrations` 11,
voice admin 4, `test_courier_carrybee` 29, courier manifests / multi / redx / pathao / accounts /
security, `test_rto`, `test_risk_network_v37`, `test_automation`; ruff + format + mypy on changed
files. **Pending (operator):**

```
cd backend
python -m pytest -q -p no:cacheprovider
TEST_DATABASE_URL=postgresql+asyncpg://ecomsbd:ecomsbd@localhost:5432/ecomsbd_test python -m pytest -q -p no:cacheprovider
DATABASE_URL=postgresql+asyncpg://ecomsbd:ecomsbd@localhost:5432/ecomsbd alembic upgrade head && alembic downgrade -1 && alembic upgrade head
python -m ruff check app tests migrations && python -m ruff format --check app tests migrations && python -m mypy
```
