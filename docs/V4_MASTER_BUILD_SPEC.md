# ecomsbd V4 — Master Build Spec

> **Where this file lives:** copy it to `docs/V4_MASTER_BUILD_SPEC.md` in the repo.
> **Who reads it:** Claude Code, one work package (WP) at a time. Do **not** read this
> whole file in one go — read the "Global rules" once per session, then only the
> section of the WP you are working on (search for its `### WP` heading).
> **Progress lives in:** `docs/build/PROGRESS.md` (the ledger). The ledger is the
> source of truth for what is done; this file is the source of truth for what to build.

Written 2026-11-17 against `main` @ `711f917` (v3.0.6+310, migration head `a39001`).

---

## 0. Why V4 exists

ecomsbd V3 is engineering-complete but has 10+ installs and 0 paying sellers. The
core loop (order → courier → COD reconcile) has never run against a real courier,
the paste parser fails on real Banglish messages, and the two things sellers ask for
first — **fraud ratio** and **invoice/label print** — are missing.

V4 does three things, in this order:

1. **Make the core loop real** (Phase 0): LLM parser, delivery charge rules, invoice +
   label print, fraud-ratio adapter, crash + funnel visibility, docs truth.
2. **Smart Order Pipeline** (Phases 1–6): orders are detected automatically, the
   customer is called to confirm, and a confirmed order books itself.
3. **Revenue** (Phase 2 and 7): seller payment by bKash/Nagad TrxID, call credits,
   more couriers, SMS gateway.

The product promise V4 must make true:

> "Order ashle ecomsbd nije dhore, customer ke call kore confirm kore, ar confirm hole
> courier e book kore label ready kore dey."

---

## 1. Global rules (read every session)

### 1.1 Precedence

Financial correctness → security → data integrity → this spec's locked decisions →
`docs/CODPILOT_CLAUDE_CODE_FINAL_MASTER_SPEC.md` product rules → UI prototype.
Where this spec and the old master spec conflict, this spec wins **except** on
financial correctness, security and data integrity.

### 1.2 Existing invariants (never break)

- Money is integer **paisa**. No float anywhere in a money path.
- Every business table is tenant-owned through `app/db/tenancy.py`; new tables get
  RLS on and client-role grants revoked in their migration (copy the pattern from
  `a39001`).
- The financial ledger and the stock ledger are append-only. V4 adds **no** code path
  that edits either; stock and money only move through their existing services.
- Side effects go through the outbox. **Every new outbox topic gets a handler** in the
  same commit, or the outbox head blocks.
- Money-touching and provider-touching requests are idempotent (`Idempotency-Key` /
  unique business keys).
- Secrets and phone numbers never reach a log, an exception message, Sentry, or a
  test snapshot. Message text from customers is never logged.
- **An unverified provider capability is unsupported.** No provider endpoint, field,
  signature scheme, status or price is invented. If a contract document is missing,
  build the port + fake + tests, mark the WP `BLOCKED: <CODE>` in the ledger, and move on.
- Webhook verifiers **refuse everything** when their secret is not configured.
- An estimate never renders as exact. A guess never silently replaces a better source.
- Order save never contacts a courier. Booking happens only through the existing
  booking service / automation `BOOK_COURIER` action.
- Cross-tenant access returns 404, logs at ERROR.
- Every new feature ships behind a feature flag that **defaults off** in
  `app/common/feature_flags.py`.

### 1.3 The 12 locked decisions (do not re-decide)

| # | Decision |
|---|---|
| L1 | Every order source (paste, share, phone notification, Chrome extension, Meta webhook, website) enters one **Intake service** and produces a `chat_order_drafts` row. No second draft system. |
| L2 | Every linked phone or extension is an `integration_connections` row with provider `DEVICE`. Health, revoke and kill switch come from the existing Integrations Hub. |
| L3 | A device authenticates captures with a **scoped ingest key** (scope `intake.capture`), not a seller session. |
| L4 | Detection runs **on the device** (rules + score). Only order-like messages are uploaded; without Autopilot consent, only after the seller taps. |
| L5 | Detection rules, app package list and extension DOM selectors come from a **versioned server config** (`intake_rule_sets`). UI changes at Meta/WhatsApp are fixed by config, not by an app release. |
| L6 | Primary phone UI is a **heads-up notification + translucent quick-order activity**. No `SYSTEM_ALERT_WINDOW`, no Accessibility Service, no `READ_CALL_LOG`, no `READ_SMS`. |
| L7 | Customer confirmation is its own entity, **`order_confirmations`**, separate from order status. |
| L8 | Voice goes through a provider-neutral **`VoiceTransport`** port + YAML capability manifest, mirroring `SmsTransport` and courier manifests. |
| L9 | Who gets called is a **deterministic, explainable policy** with a stored reason code. No ML score. |
| L10 | Booking after confirmation uses the **existing V3.4 automation engine** (`BOOK_COURIER`). No new booking path. |
| L11 | **Autopilot is opt-in**, default off, behind shop-level and order-level gates. Any gate failure sends the order to the seller; it never books on a guess. |
| L12 | The LLM parser only **extracts fields** into a strict schema. It never chooses actions. Customer text is data, never instructions. |

### 1.4 Conventions

- Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic. Ruff + ruff format + mypy strict.
- New backend packages live under `backend/app/<name>/` with `models.py`, `service.py`,
  and a router in `backend/app/api/v1/<name>.py`, registered in `app/api/v1/router.py`.
- Migrations: one per phase, ids `a40001`, `a41001`, `a42001`, `a43001`, `a44001`,
  `a45001`, named `YYYYMMDD_0100_<id>_<slug>.py`, single head, portable
  (ADR 0004), downgrade implemented.
- Flutter: Riverpod, Dio client in `lib/core/api`, every string in EN + BN
  (`lib/l10n/`), 360×800 overflow test for every new screen.
- Web: Next.js app under `apps/web/app/(app)/`, EN + BN copy, `npm test` covers new copy keys.
- Error codes: stable machine code + Bangla message + `retryable`, in the existing taxonomy.
- Worker jobs: registered in `app/worker/main.py`, idempotent, tenant-scoped session per shop.

### 1.5 Definition of done (every WP)

1. Code + migration (if any) + tests for every rule in the WP's "Must" list.
2. `cd backend && python -m pytest -q` green; `python -m ruff check app tests migrations`,
   `python -m ruff format --check app tests migrations`, `python -m mypy` clean.
3. Flutter WPs: `flutter analyze` clean, `flutter test` green, `dart format --set-exit-if-changed lib test`.
4. Web WPs: `npm run typecheck && npm run lint && npm test` green.
5. Flag added, default off. `.env.example` + `.env.production.example` updated for any new variable (`# REQUIRED WHEN ...`).
6. `docs/IMPLEMENTATION_STATUS.md` row added/updated; runbook added in `docs/runbooks/` for any new job, provider or webhook (detection, containment, flag, recovery, verification).
7. Ledger updated: status, commit SHA, what was verified, what is blocked.

Phase end (not every WP): PostgreSQL suite
(`TEST_DATABASE_URL=postgresql+asyncpg://ecomsbd:ecomsbd@localhost:5432/ecomsbd_test python -m pytest -q`)
and `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`.

### 1.6 Session rules for Claude Code (every session follows these)

**Loop per WP**
1. Plan in ≤ 10 bullets inside `PROGRESS.md` "Now" (not in chat).
2. Explore narrowly: `rg`/grep for symbols, read only needed line ranges. Never open
   `docs/CODPILOT_*` or other large docs whole. Explore subagent only for searches spanning
   more than 5 directories.
3. Build in vertical slices: model → migration → service → API → tests → client. Edit; never
   rewrite a whole existing file to change part of it.
4. Narrowest tests while working (`pytest path::test -q`, `flutter test <file>`), long output
   piped through `tail -n 30`. Full backend suite + ruff + format + mypy once per WP end.
   PostgreSQL suite + migration round trip once per phase end.
5. Commit each green slice (conventional message); push the branch at WP end.
6. Update `PROGRESS.md` (status, sha, verified, notes, next step, one log line).
7. Phase end: check the gate. Real-world evidence needed → "waiting on operator" + exact
   steps under "Operator actions waiting", then continue with the next phase's code-only work.

**Hard rules**
- §1.2 invariants and §1.3 locked decisions are final.
- Missing contract → port + fake + tests, `BLOCKED: <CODE>` or `PARTIAL`, operator action, move on.
- Never touch production: no prod migrations, secrets, `.env.production*`, keystores, pushes
  to `main`, force pushes or branch deletion.
- No secrets or real phone numbers in code, tests, logs or commits (tests use `017000000xx`).
- Spec silent → most conservative option consistent with §1, record it under
  "Decisions made during build", continue. Ask the operator only before destructive or
  irreversible actions.
- Never weaken or delete a previously green test to make it pass.
- Same error 3 times → record error + attempts in the ledger, mark `PARTIAL`, next WP.

**Credit discipline**
- After 2 WPs, or before context gets long: ledger fully updated, commit, say
  "Checkpoint saved — start a new session with Prompt B.", stop.
- Chat output per WP: 3 lines (WP id, result, next). No long explanations in chat.
- Finish: when no WP is TODO/IN_PROGRESS, write a final summary at the top of `PROGRESS.md`
  (done / partial / blocked codes / operator actions), update `docs/IMPLEMENTATION_STATUS.md`,
  report in ≤ 10 lines.

---

## 2. Phase 0 — Make the core loop real

Migration `a40001`. Branch `v4/phase-0`.

### WP0.1 — Docs truth

**Must**
- Android package is `com.ecomsbd.app` everywhere: `README.md`, `docs/RELEASE_READINESS.md`,
  `docs/V3_CLOUD_HANDOFF.md`, `docs/PRODUCTION_ENV_SETUP.md`, `apps/mobile_flutter/env/README.md`.
  Keep historical mentions only inside clearly marked "history" notes.
- Rewrite the top of `docs/IMPLEMENTATION_STATUS.md` so it reflects V3.1–V3.8, chat-to-order,
  the web app, Free Launch Mode, current test counts, and links this spec.
- Create `docs/build/PROGRESS.md` from the template in §10 if it does not exist.

### WP0.2 — Crash + funnel visibility

**Must**
- Add `sentry_flutter` to the app, initialised only when `ECOMSBD_SENTRY_DSN` is non-empty;
  `sendDefaultPii = false`; a `beforeSend` that strips phone numbers (reuse the Dart masking
  helper) and drops breadcrumbs carrying message text.
- **Server-side funnel events** (no client analytics SDK): table `product_events`
  (`tenant_id`, `user_id` nullable, `name`, `occurred_at`, `props` JSON with no PII),
  unique `(tenant_id, name)` for "first_*" events. Emit from existing services:
  `shop_created`, `first_order`, `first_courier_connected`, `first_booking`,
  `first_payout_import`, `first_reconcile`, `first_capture`, `first_confirmation_call`,
  `first_autopilot_order`, `plan_paid`. Plus `weekly_active` once per ISO week per shop.
- Admin endpoint `GET /v1/admin/ops/funnel?from=&to=` returning counts per step (SUPPORT role).

### WP0.3 — LLM order parser (`EnhancedOrderParser`)

**Must**
- Implement `EnhancedOrderParser` in `app/orders/parser/` behind flag `ai_parse_enabled`
  and entitlement `ai_parse_monthly`.
- Flow (operator correction 2026-11-18, supersedes the earlier "LLM only when
  `is_low_confidence`" rule): the deterministic parser's job is **phones + explicitly
  labelled fields only**; it is not tuned further. Call the LLM unless every non-phone field
  (name, address, items, cod) has confidence `>= 0.8` (i.e. came from an explicit label);
  an empty field also triggers the call.
- Merge: phones are always deterministic (an LLM phone is kept only if it normalises and
  appears in the text). For name/address/items/cod: keep the deterministic value when its
  confidence is `>= 0.8`, otherwise take the LLM value. When both exist and disagree on a
  labelled field, keep the deterministic one and add the field to `uncertain_fields`.
- If the address is empty and the phone matches an existing customer of this shop, prefill
  that customer's last delivery address and mark `address` uncertain.
- Provider port `AiParseClient` with one implementation `AnthropicParseClient`
  (`httpx`, no SDK dependency required). Config: `AI_PARSE_PROVIDER=anthropic`,
  `AI_PARSE_API_KEY`, `AI_PARSE_MODEL` (example value in `.env.example`: a Haiku-class
  model id), `AI_PARSE_TIMEOUT_SECONDS=8`, `AI_PARSE_MAX_INPUT_CHARS=6000`.
- Use tool-use / JSON schema output with exactly these fields: `customer_name`,
  `phones[]`, `address`, `district`, `thana`, `items[{name, quantity, size, color, unit_price_paisa}]`,
  `cod_amount_paisa`, `notes`, `field_confidence{}`. Validate with Pydantic; any schema
  violation = discard the LLM result and keep the deterministic one.
- Prompt-injection guard: system prompt states the text is untrusted data; the text is
  wrapped in a delimited block; the model has no tools that act; output never contains
  instructions that are executed. Phones from the LLM must also pass the existing phone
  normaliser and must appear (normalised) in the source text — otherwise dropped.
- Table `ai_usage` (`tenant_id`, `purpose`, `model`, `input_tokens`, `output_tokens`,
  `cost_micro_usd`, `latency_ms`, `outcome`, `created_at`). Price per token from config
  `AI_PARSE_PRICE_INPUT_PER_MTOK_USD` / `..._OUTPUT_...` (labelled estimate).
- Cache by SHA-256 of normalised text per tenant for 24 h.
- Outage, timeout or quota exhausted → deterministic result + warning
  `AI_PARSE_UNAVAILABLE`; order entry never blocks.
- **Test corpus**: `backend/tests/fixtures/parse_corpus/*.yaml` — at least the 5 cases
  below plus 45 more you write in the same style (Bangla, Banglish, English, mixed digits,
  two phones, missing address, price in text, "ta/টা/pcs", sizes, colours). Each case has
  expected fields. Tests run the deterministic parser always, and the LLM path with a fake
  client returning recorded JSON. Add a script `python -m app.orders.parser.eval --live`
  that runs the corpus against the real API (operator-run, prints field accuracy; never in CI).

Corpus seed cases (expected output in brackets):

```
1. "নাম: রহিম উদ্দিন\nফোন: 01712345678\nঠিকানা: বাড়ি ১২, রোড ৫, মিরপুর ১০, ঢাকা\nকালো পাঞ্জাবি XL ২টা\nমোট: ২৪৫০ টাকা"
   [name রহিম উদ্দিন · phone +8801712345678 · address বাড়ি 12, রোড 5, মিরপুর 10, ঢাকা · item কালো পাঞ্জাবি ×2 XL · cod 245000]
2. "Rahim 01812345678 Mirpur 10 Dhaka. Black panjabi XL 2 pcs. total 2450tk"
   [name Rahim · phone +8801812345678 · address Mirpur 10 Dhaka · item Black panjabi ×2 XL · cod 245000]
3. "আপু আমার নাম সুমি, ০১৯১২৩৪৫৬৭৮, ঠিকানা: আগ্রাবাদ, চট্টগ্রাম। লাল থ্রিপিস M সাইজ একটা দিয়েন। ডেলিভারি চার্জ সহ কত?"
   [name সুমি · phone +8801912345678 · address আগ্রাবাদ, চট্টগ্রাম · item লাল থ্রিপিস ×1 M · cod null · notes asks total]
4. "name- karim\nnumber 01611223344 / 01711223344\naddress- kushtia sadar, kushtia\nproduct: watch 1 ta 1200"
   [name karim · phones both, needs selection · address kushtia sadar, kushtia · item watch ×1 unit 120000]
5. "01712345678\nTangail\n2ta tshirt L"
   [name null · phone +8801712345678 · address Tangail (district only, uncertain) · item tshirt ×2 L]
```

- Also fix the deterministic parser for the label patterns seen in 2, 4, 5:
  `name-`, `address-`, `number`, a lone district line is an address (uncertain), not a name.
- **Held-out set** (operator correction 2026-11-18): the 50-case corpus was written and tuned
  by the same agent, so its score is optimistic (on 8 held-out messages the deterministic
  parser got phones 8/8, names ~1/8, items ~1/8, with `is_low_confidence` False for 7/8).
  `backend/tests/fixtures/parse_holdout/holdout.yaml` holds the operator's held-out cases. It
  is **never** used to tune the parser and is not a pass/fail test.
  `python -m app.orders.parser.eval --live --set holdout` reports deterministic-only and
  merged accuracy separately. Real masked seller messages (30–50) are added by the operator.

**Gate G0.3**: deterministic + LLM path reaches ≥ 90% field accuracy on phone, address and
items, **measured on the held-out set with the live LLM** (operator run). The corpus with
recorded responses stays a regression test in CI.

### WP0.4 — Shop delivery rules + COD auto-calc

**Must**
- Table `shop_delivery_rules` (one row per tenant): zone charges in paisa for
  `INSIDE_DHAKA`, `DHAKA_SUBURB`, `OUTSIDE_DHAKA`; `default_courier` (nullable);
  `max_auto_cod_paisa` (default 500000); risk thresholds `risk_low_bps` (5000), `trusted_network_bps` (8500), `trusted_repeat_min` (2) used by WP2.2; `quiet_start` "21:00",
  `quiet_end` "09:00"; `autopilot_daily_cap` (default 30); `call_everyone` bool;
  `call_nobody` bool.
- Zone resolver `app/orders/zones.py`: district/area text → zone, with a data file of the
  64 districts (EN + BN spellings) and Dhaka suburb list. Unknown → `null`, never guessed.
- COD calculator: `sum(catalog price × qty) + zone charge − discount`. When the chat states
  an amount: if it differs from the calculation by more than the zone charge, mark
  `cod_source=CHAT_MISMATCH` and add `cod_amount` to `uncertain_fields`.
- `GET/PATCH /v1/shop/delivery-rules` (permission `settings.manage`); mobile + web settings screen.

### WP0.5 — Invoice + shipping label

**Decision:** documents are **HTML rendered by the backend** and printed by the browser
(web) or converted to PDF by the OS print system (mobile). Reason: Bangla needs complex
script shaping, which Python PDF libraries and the Dart `pdf` package do not do correctly;
browsers and Android WebView print do.

**Must**
- `app/documents/`: Jinja-free string templates (or Jinja2 if already a dependency — check
  first; do not add a template engine just for this), with escaping on every field.
- Pure-Python **Code128-B SVG** encoder in `app/documents/code128.py` with tests against
  known vectors. No new dependency.
- Endpoints (permission `order.view`):
  `GET /v1/orders/{id}/documents/invoice` (A4), `GET /v1/orders/{id}/documents/label?size=4x6|3x4|a4-8up`,
  `POST /v1/orders/documents/bulk` (`{order_ids[], kind, size}` → one HTML, page-break per doc, max 100).
  Response `text/html; charset=utf-8`, `Cache-Control: no-store`.
- Label content: shop name + phone, customer name, **masked phone only if the seller chose
  "mask on label"** (default: full phone, the courier needs it), address, COD amount large,
  courier name, consignment/tracking id as barcode (order number if not booked), item summary,
  order number, date. Invoice: line items, delivery charge, discount, total, "COD" badge.
- Bangla font: bundle Noto Sans Bengali (OFL) as a web font served from the web app /
  embedded via `@font-face` data URI in the HTML.
- Web: "Print invoice" / "Print label" / bulk print from the orders table.
- Mobile: add the `printing` package; render the HTML with `Printing.convertHtml` → share
  or print. Test on 360×800.

### WP0.6 — Fraud ratio adapter (external risk)

**Must**
- Pick the provider in this order: BD Courier API (`bdcourier.com`), else another licensed
  provider the operator names. Its contract must be transcribed into
  `docs/providers/<provider>/CONTRACT.md` **from documentation the operator supplies**.
- If the contract file does not exist → do the provider-independent parts below, then mark
  `BLOCKED: EXTERNAL_RISK_CONTRACT_REQUIRED` and continue.
- Provider-independent parts: normalised fact `courier_history`
  (`provider_counts[{courier, delivered, cancelled, total}]`, `aggregate`, `as_of`),
  rendering on the order card as a chip ("Steadfast 15/17 · Pathao 8/9"), on the customer
  page and in the quick-order sheet; sample-size floor (< 3 parcels = "kom data").
- Adapter in `app/risk_providers/` using the existing port, cache TTL, daily cap, health,
  `customer.external_risk_lookup` permission, automation condition `external_success_bps`.
- Never scrape a courier merchant panel with the seller's password.

### WP0.7 — Courier live-readiness

Code-side only; live calls are an operator step.

**Must**
- `python -m app.provider_smoke` supports `pathao` and `redx` with the same three levels as
  Steadfast (`read-only`, `--probe-undocumented`, `--allow-create`).
- Pathao location resolver: city/zone/area lookup cached per tenant, fuzzy match from the
  draft address, `null` when ambiguous (Autopilot gate uses this).
- Statement profiles: `app/payouts/profiles/` with a loader; a profile is added only from a
  real file placed in `backend/tests/fixtures/statements/<provider>/` by the operator.
  If none exists → `BLOCKED: STATEMENT_SAMPLE_REQUIRED`.
- Ledger lists the operator checklist: Steadfast keys → smoke → flip `steadfast_enabled`.

**Gate G0** (phase): G0.3 met; invoice/label print works on web and Android; Sentry receives
a test event with phone redacted; at least one courier marked live by the operator in the ledger.

---

## 3. Phase 1 — Intake (auto-detect)

Migration `a41001`. Branch `v4/phase-1`. Flags: `intake_device_enabled`, `extension_enabled` (Phase 5).

### WP1.1 — DEVICE connections + ingest keys

**Must**
- `integration_connections.provider = "DEVICE"`; `config` holds `platform` (`android`,
  `extension`), `app_version`, `covered_packages[]`, `last_heartbeat_at`, `listener_state`.
- Table `device_ingest_keys`: `connection_id`, `key_hash` (SHA-256), `key_last4`, `scope`
  (`intake.capture`), `created_by`, `revoked_at`. Key format `edk_` + 40 url-safe chars,
  shown once.
- `POST /v1/devices/link` (seller session; creates connection + key; idempotent per install id).
- `POST /v1/devices/pair` (web, seller session) → 6-digit code, 10 min, one use;
  `POST /v1/devices/pair/claim` (unauthenticated, rate-limited per IP) → key.
- Auth dependency `require_ingest_key(scope)` that resolves tenant + connection; it is valid
  **only** on intake routes. A test asserts every other route rejects it.
- Revoking the connection, removing the team member who linked it, or account deletion
  revokes the key.

### WP1.2 — Intake rules config

**Must**
- Table `intake_rule_sets` (global, not tenant-owned; admin-managed): `version`, `payload` JSON,
  `active`, `created_by`. Seed version 1 in the migration.
- Payload v1: `packages` (`com.facebook.pages.app` Business Suite, `com.whatsapp.w4b`,
  `com.whatsapp`, `com.facebook.orca`) each with `default_enabled`; phone regex set (Bangla +
  ASCII digits, `+88`, separators); intent keywords BN/Banglish/EN; address keywords + district
  list reference; score weights; threshold; buffer window 25 min; extension selectors per host.
- `GET /v1/intake/rules` with ETag; devices cache it and refresh daily or on 304-miss.
- Per-shop overrides: `covered_packages` (set by the server when an official Meta connection is
  active — L1/coverage rule) and seller toggles.
- Admin: `GET/POST /v1/admin/intake-rules` (OPS), new version is inactive until activated.

### WP1.3 — Capture endpoint + Intake service

**Must**
- `app/intake/service.py` is the single entry: `capture(tenant, source, conversation_key,
  sender_display, messages[], connection)` → upserts `chat_identities` (external_key = HMAC of
  `package|conversation_key`) and `chat_messages` (provider_message_id = device message hash),
  then lets the existing chat-order worker fold them into drafts.
- Add `capture_source` to `chat_messages` and `chat_order_drafts`
  (`META_WEBHOOK`, `DEVICE_NOTIFICATION`, `SHARE`, `EXTENSION`, `PASTE`); backfill existing rows
  as `META_WEBHOOK`.
- Refactor `app/chat_orders/capture.py` so the Meta path also calls the Intake service. No
  behaviour change for Meta; existing tests stay green.
- `POST /v1/intake/captures` (ingest key): batch ≤ 50 messages, each ≤ 4,000 chars;
  `Idempotency-Key` = capture batch id; per-message dedupe by hash; rate limit 120/min/device.
- `POST /v1/intake/captures/manual` (seller session): share target and quick-order paste.
- `POST /v1/intake/heartbeat` (ingest key): updates `listener_state`, `app_version`.
- Worker `scan_device_health` (hourly): no heartbeat for 24 h → connection `NEEDS_ATTENTION`
  + one notification per 24 h.
- Drafts from devices follow the same states, matching, expiry (30 days) and privacy rules as
  chat-to-order. Raw text is deleted after 30 days by the existing housekeeping job (extend it).
- Duplicate merge: a new draft whose selected phone matches an open draft or an order created
  in the last 24 h for the same tenant attaches to it instead of creating a second one.
- Outbox topic `intake.draft_ready` (with handler) when a draft first becomes `READY_FOR_REVIEW`.

### WP1.4 — Android Notification Listener (native)

**Must**
- Kotlin in `android/app/src/main/kotlin/com/ecomsbd/app/intake/`:
  `OrderNotificationListener : NotificationListenerService`, `IntakeClassifier`,
  `ConversationBuffer` (Room is not used; store in an encrypted file or `EncryptedSharedPreferences`
  as JSON, capped at 200 entries), `IntakeUploader` (WorkManager, exponential backoff),
  `IntakeChannel` (MethodChannel `ecomsbd/intake` for Flutter).
- Manifest: the listener service with `BIND_NOTIFICATION_LISTENER_SERVICE`; WorkManager
  dependency. **No** overlay, accessibility, call-log or SMS permission. Update the manifest
  comment block listing deliberately absent permissions.
- Read `Notification.EXTRA_MESSAGES` (MessagingStyle) when present; else title + text.
  Ignore group summary notifications, own app, ongoing notifications, and packages not enabled.
- Classifier = server rules; phone match is mandatory; score ≥ threshold = order-like.
- Buffer per `package|conversation title` for the rule's window; one heads-up per conversation,
  updated in place (same notification id) as messages arrive.
- Heads-up (channel `order_detect`, importance high): title "<sender> — order mone hocche",
  body "01712•••78 · <area guess>", actions **Create**, **Ignore**, **Ei sender ke ar na**
  (stores HMAC of sender locally).
- **Create** → `QuickOrderActivity` (translucent theme, Flutter route `/quick-order` via a cached
  engine) with the buffered text, and uploads the buffer through `captures/manual`.
- Autopilot consent on (§6) → order-like buffers upload automatically through `captures`
  with the ingest key, no tap. Build the path now; it stays inactive until the server
  reports `autopilot_on` for the shop (Phase 4).
- Heartbeat every 6 h via WorkManager; also on listener connect/disconnect.
- Settings → "Auto order detect" screen (Flutter): disclosure (what is read, what is sent, how to
  stop), button to system Notification Access, per-app toggles, status (connected / last
  heartbeat), brand-specific auto-start guide (Xiaomi, Oppo, Vivo, Realme, Samsung) shown when
  heartbeat is stale.
- Tests: Kotlin unit tests for classifier + buffer (JVM), Flutter tests for settings and quick
  order screens, a fake MethodChannel.

### WP1.5 — Quick-order sheet + share target

**Must**
- Flutter `features/intake/quick_order_screen.dart`: parsed fields (LLM path), uncertain
  fields highlighted, phone picker when > 1, product match with picker for `AMBIGUOUS`,
  COD auto-calc from WP0.4 with mismatch warning, fraud chip (WP0.6), courier suggestion
  (shop default, else best delivered rate in that zone from the shop's own history with the
  sample rule), duplicate warning.
- Buttons: **Confirm & call customer** (creates order + `REQUEST_CONFIRMATION`),
  **Already confirmed in chat** (creates order + confirmation resolved by `SELLER_CHAT`),
  **Save draft**. Until Phase 2 lands, the first two collapse into one **Create order**
  button (plain order create); wire the confirmation buttons in WP2.1 behind
  `confirmation_engine_enabled`.
- "Aro message lagbe" merges clipboard text into the draft (user paste only).
- Android share target (`ACTION_SEND text/plain`) and iOS Share Extension → same screen.
- Offline: create works through the existing outbox; confirmation request is queued and sent on reconnect.

**Gate G1**: 10 pilot sellers × 7 days; precision = Create ÷ (Create + Ignore) ≥ 70%;
0 listener crashes in Sentry; Play closed-testing review passed with the disclosure.

---

## 4. Phase 2 — Confirmation engine + manual payments

Migration `a42001`. Branch `v4/phase-2`. Flags: `confirmation_engine_enabled`,
`confirmation_sms_link_enabled`, `manual_payment_enabled`.

### WP2.1 — `order_confirmations`

**Must**
- Tables:
  - `order_confirmations`: `order_id`, `state`, `decision` (`AUTO_CONFIRM`, `CALL`, `SELLER_REVIEW`),
    `reason_code`, `method` (`IVR`, `SMS_LINK`, `MANUAL_CALL`, `SELLER_CHAT`, `AUTO_TRUSTED`, `SELLER`),
    `attempt_count`, `next_action_at`, `resolved_at`, `resolved_by`, `superseded_by`,
    `order_version` (snapshot), `cod_paisa_snapshot`. Partial unique: one non-terminal row per order.
  - `confirmation_attempts`: `confirmation_id`, `attempt_no` (unique pair), `channel`
    (`IVR`, `SMS_LINK`, `MANUAL_CALL`), `provider`, `provider_ref`, `status`, `dtmf`,
    `duration_seconds`, `cost_paisa` (nullable, provider-reported), `cost_quality`
    (`ACTUAL`/`ESTIMATED`), `started_at`, `ended_at`.
  - `confirmation_links`: `confirmation_id`, `token_hash`, `expires_at`, `used_at`, `result`.
- States: `PENDING`, `CALL_QUEUED`, `CALLING`, `NO_ANSWER`, `SMS_LINK_SENT`, `SELLER_REVIEW`,
  terminal `CONFIRMED`, `CANCELLED`, `SUPERSEDED`. Transitions exactly:
  - `PENDING → CONFIRMED` (AUTO_TRUSTED) | `CALL_QUEUED` | `SELLER_REVIEW`
  - `CALL_QUEUED → CALLING` (voice enabled) | `SELLER_REVIEW` (voice disabled/unavailable → manual queue)
  - `CALLING → CONFIRMED` (dtmf 1) | `CANCELLED` (dtmf 2) | `SELLER_REVIEW` (dtmf 3, or ambiguous result `UNKNOWN`) | `NO_ANSWER`
  - `NO_ANSWER → CALL_QUEUED` (attempts < max, after 30 min) | `SMS_LINK_SENT` (link enabled) | `SELLER_REVIEW`
  - `SMS_LINK_SENT → CONFIRMED | CANCELLED` (link) | `SELLER_REVIEW` (expired, 6 h)
  - `SELLER_REVIEW → CONFIRMED | CANCELLED` (seller) | `SMS_LINK_SENT` (cause `SELLER_NO_ANSWER`: the seller's own call unanswered, link enabled, once per confirmation — operator decision 2026-11-20)
  - any non-terminal → `SUPERSEDED` (order edited: items changed or COD changed > 10%; a new
    confirmation starts) | `CANCELLED` (order cancelled)
- A state machine class with an explicit transition table; a test enumerates every illegal transition.
- On `CONFIRMED`: `OrderService` moves the order `DRAFT → CONFIRMED` with actor
  `system:customer_confirmation` (or the seller) — only if the order is still `DRAFT` and its
  `version` equals `order_version`; otherwise record and raise a seller attention item.
- On `CANCELLED` by the customer: order → `CANCELLED` with reason `CUSTOMER_DECLINED`; the
  customer gets no flag automatically.
- Outbox topics (with handlers): `confirmation.confirmed`, `confirmation.cancelled`,
  `confirmation.no_answer`, `confirmation.callback_requested`, `confirmation.review_needed`.

### WP2.2 — Policy

**Must**
- `app/confirmations/policy.py`, pure function `decide(facts, rules) -> (decision, reason_code)`;
  first match wins, in this order:
  1. customer flag `BLOCKED` → `SELLER_REVIEW` / `CUSTOMER_BLOCKED`
  2. external success < `risk_low_bps` (default 5000) with ≥ 5 parcels, or own-shop band HIGH → `SELLER_REVIEW` / `HIGH_RISK`
  3. COD > `max_auto_cod_paisa` → `CALL` / `HIGH_VALUE`
  4. own-shop ≥ `trusted_repeat_min` delivered (default 2) and band LOW → `AUTO_CONFIRM` / `TRUSTED_REPEAT`
  5. external ≥ `trusted_network_bps` (default 8500) with ≥ 5 parcels and no own-shop return → `AUTO_CONFIRM` / `TRUSTED_NETWORK`
  6. else → `CALL` / `VERIFY_NEW`
  - Overrides: `call_nobody` → rules 3–6 become `SELLER_REVIEW` / `SHOP_MANUAL`; `call_everyone` → rules 4–5 become `CALL`.
  - External provider unavailable → rules 2 (external part) and 5 are skipped.
- Thresholds live in `shop_delivery_rules`; order of rules is not configurable.
- Facts and decision are stored on the confirmation row for "why" display.

### WP2.3 — Manual call queue + SMS link

**Must**
- `GET /v1/confirmations/queue` (permission `order.write`): `SELLER_REVIEW` + `CALL_QUEUED`
  when voice is off, ordered by COD desc then age.
- `POST /v1/orders/{id}/confirmation/{action}`: `mark_confirmed`, `mark_cancelled`,
  `call_now` (respects quiet hours unless `override_quiet=true`, audited), `mark_no_answer`.
- Mobile "Call queue" screen: tap → `tel:` intent → on resume, outcome sheet
  (Confirm / Cancel / Dhoreni). No call-log permission.
- SMS link: when enabled and `SmsTransport` is configured, after max call attempts send
  `"[shop]: order ৳<amount>. Confirm/cancel: <PUBLIC_WEB_URL>/c/<token>"` (Banglish, single
  GSM segment target; segment count recorded). Token 22 url-safe chars, stored hashed, 6 h.
- Web public page `apps/web/app/c/[token]/page.tsx`: GET shows shop name, item summary, COD,
  masked phone, two buttons; **GET changes nothing**. `POST /v1/public/confirmations/{token}`
  `{result: confirm|cancel}` — one use, rate-limited, same answer shape for unknown tokens.

### WP2.4 — Automation hooks

**Must**
- Triggers: `confirmation.confirmed`, `confirmation.cancelled`, `confirmation.no_answer`,
  `confirmation.callback_requested`, `intake.draft_ready`.
- Actions: `REQUEST_CONFIRMATION` (creates the confirmation, runs the policy) and
  `CONVERT_DRAFT_TO_ORDER` (Autopilot only; Phase 4 implements the gate — until then it
  returns `SKIPPED/AUTOPILOT_DISABLED`).
- Recipes (EN + BN): "Order → confirm customer" (`order.created` → `REQUEST_CONFIRMATION`);
  "Confirmed → book courier → tracking SMS" (trigger `confirmation.confirmed`, existing
  `BOOK_COURIER` + `SEND_TEMPLATE`).
- Web builder + mobile recipe list show the new triggers/actions; `npm test` copy check passes.

### WP2.5 — Seller payment by bKash/Nagad TrxID

**Must**
- Table `billing_payment_submissions`: `tenant_id`, `plan_code`, `months`, `amount_paisa`,
  `method` (`BKASH`, `NAGAD`), `payer_number_masked`, `trx_id` (unique per method),
  `state` (`SUBMITTED`, `APPROVED`, `REJECTED`), `reviewed_by`, `reason`.
- Seller screen (mobile + web, hidden while `FREE_LAUNCH_MODE` is on): shows the operator's
  receiving numbers from config `MANUAL_PAYMENT_ACCOUNTS` (JSON), amount, and a TrxID form.
- Admin: `GET /v1/admin/payments`, `POST /v1/admin/payments/{id}/approve|reject`; approve
  calls the existing `AdminManualBillingProvider` grant with absolute period end; idempotent.
- Notification to the seller on approve/reject. Duplicate TrxID refused.

**Gate G2**: on pilot shops ≥ 80% of new orders end with a recorded confirmation
(any method); one manual payment approved end to end in staging.

---

## 5. Phase 3 — Voice IVR

Migration `a43001`. Branch `v4/phase-3`. Flag `voice_confirmation_enabled`.

### WP3.1 — `VoiceTransport` port + manifest

**Must**
- `app/voice/transport.py`: `VoiceTransport` Protocol — `availability()`, `place_call(VoiceCall)`,
  `verify_callback(headers, body)`, `parse_callback(body) -> list[VoiceEvent]`. `VoiceCall`
  has `to_e164`, `audio_ref` (pre-uploaded) or `segments[]` (only if `dynamic_audio`),
  `dtmf_options`, `max_retries=0` (we retry, not the provider), `client_ref`.
- `DisabledVoiceTransport` returns `NOT_CONFIGURED`; `MockVoiceTransport` for tests.
- `app/voice/manifests/<provider>.yaml`: `dtmf_capture`, `dynamic_audio`, `callback_signature`,
  `caller_id`, `max_call_seconds`, `price_per_call_paisa` (or per minute) with
  `verified_at` + evidence per capability, `blockers[]`. Loader mirrors courier manifests.
- `voice_provider_connections` (platform-level, not tenant-owned): encrypted credentials,
  health + circuit breaker (reuse `app/common/provider_health.py`).
- Provider adapter is written **only from a contract the operator supplies** in
  `docs/providers/voice/<provider>/CONTRACT.md`. None → `BLOCKED: VOICE_PROVIDER_CONTRACT_REQUIRED`
  after finishing everything else in this phase with the mock.

### WP3.2 — Call orchestration

**Must**
- Worker `dispatch_confirmation_calls` (every 30 s): picks `CALL_QUEUED` rows whose
  `next_action_at <= now`, outside quiet hours (shop timezone Asia/Dhaka), shop not paused,
  credit available; `SELECT … FOR UPDATE SKIP LOCKED`; marks `CALLING` + attempt row in one
  transaction **before** the provider call; idempotency `client_ref = confirmation:{id}:{attempt}`.
- First call 2 min after the confirmation is created. No answer → +30 min, max 2 calls,
  then SMS link (if enabled) else `SELLER_REVIEW`.
- Ambiguous provider result (timeout, 5xx, crash while `CALLING`) → attempt `UNKNOWN`,
  confirmation `SELLER_REVIEW`; **never** an automatic re-call. A `CALLING` row older than
  10 min with no callback is reconciled the same way.
- Callback route `POST /v1/webhooks/voice/{provider}`: raw body persisted, verified, deduped on
  `(provider, provider_ref, event)`, then applied. Late DTMF after a terminal state is recorded
  and ignored.
- DTMF 3 → push `CHAT_CALLBACK_REQUESTED` to owner/manager with a call button.
- Per-customer cap: max 3 confirmation calls per phone per day across all orders of the shop.

### WP3.3 — Shop intro audio

**Must**
- Script (BN): "আসসালামু আলাইকুম। আপনি <shop name> থেকে একটি অর্ডার করেছেন। অর্ডার কনফার্ম করতে ১ চাপুন। বাতিল করতে ২ চাপুন। দোকানের সাথে কথা বলতে ৩ চাপুন।"
- Audio is generated once per shop name change by a TTS port (`TtsClient`; provider from
  operator contract, else `BLOCKED: TTS_PROVIDER_REQUIRED` and the operator uploads an MP3/WAV).
  Stored in R2 (`voice/<tenant>/intro-v<n>.wav`), uploaded to the voice provider if required.
- Seller previews it in Settings before voice can be enabled.
- If the manifest says `dynamic_audio: true`, append "আপনার অর্ডারের মূল্য <amount> টাকা।" with
  segments; otherwise the amount is not spoken (SMS/link carries it).

### WP3.4 — Credits

**Must**
- Entitlement `voice_calls_monthly` (Free 0, Starter 50, Pro 200) metered per **placed** call
  (attempt reaching the provider), atomic like existing usage counters.
- Table `voice_credit_packs` (`tenant_id`, `calls`, `remaining`, `expires_at` 90 days,
  `source` = admin grant / payment submission). Consumption order: monthly allowance, then
  oldest pack.
- A **global** daily cap `VOICE_DAILY_GLOBAL_CAP` (default 300) applies in every mode (operator
  decision 2026-11-20). Free Launch Mode: unlimited plan, so a per-shop cap `VOICE_DAILY_SHOP_CAP`
  (default 30) also protects the operator's bill; paid shops stay limited by allowance + packs.
- No credit → confirmation goes to the manual queue with reason `NO_VOICE_CREDIT`.
- Admin report: calls, answer rate, confirm rate, cost per shop per day.

**Gate G3**: 200 real calls; answer rate, confirm rate and cost per call recorded in the
ledger; pack price set to 2 × measured cost (operator decision).

---

## 6. Phase 4 — Autopilot

Migration `a44001` (settings columns only, if needed). Branch `v4/phase-4`. Flag `autopilot_enabled`.

**Must**
- Shop setting `autopilot_on` + `autopilot_consent_at` + `autopilot_paused_until`.
- Shop-level gate (all true, shown as a checklist in Settings, re-checked every run):
  a courier connection `HEALTHY` whose provider is marked live-verified; `shop_delivery_rules`
  zone charges set and `default_courier` set; voice available (or seller accepts manual queue
  mode: then Autopilot only creates orders + confirmations, no booking without confirmation);
  ≥ 20 orders confirmed in the shop; consent recorded; entitlement `autopilot`.
- Order-level gate in `CONVERT_DRAFT_TO_ORDER` (each failure → draft stays for the seller with
  a reason code): exactly one valid phone; zone resolved and (for Pathao/RedX) location ids
  resolved; every item `MATCHED` with stock; COD calculated and no `CHAT_MISMATCH`; no open
  draft/order for the phone in 24 h; policy decision not `SELLER_REVIEW`; daily cap not hit.
- Recipe "Autopilot" (installed disabled): `intake.draft_ready` → `CONVERT_DRAFT_TO_ORDER` →
  `REQUEST_CONFIRMATION`; booking happens through the Phase 2 "Confirmed → book" recipe.
- Kill switches: global flag; per-shop toggle; "Pause 1 hour" on Home (queued calls hold, not cancel).
- Home card: today's Autopilot count, confirmed, cancelled, waiting, and last 5 decisions with reason.

**Gate G4**: 5 pilot shops × 14 days, 0 wrong bookings (a booking whose customer did not
confirm or whose item/amount differs from the confirmed snapshot).

---

## 7. Phase 5 — Chrome extension

Branch `v4/phase-5`. New app `apps/extension` (MV3, TypeScript, Vite or plain `tsc`; no
framework heavier than needed). Flag `extension_enabled`.

**Must**
- Pairing with the 6-digit code (WP1.1); key in `chrome.storage.local`; unpair button.
- Hosts: `business.facebook.com`, `www.messenger.com`, `web.whatsapp.com` only.
- Content script reads **only the open conversation**, using selectors from `/v1/intake/rules`;
  `MutationObserver` highlights phone numbers; no reading of other chats, no background crawling.
- Side panel: same fields as the quick-order sheet (calls existing APIs with the ingest key for
  capture, then opens `app.ecomsbd.com/quick-order?draft=<id>` for the authenticated steps —
  the ingest key is never used for anything but capture).
- Selector failure → panel says "Ei page ekhon support na; text select kore 'Capture' korun"
  and capture works from the text selection.
- Store listing copy + privacy disclosure in `apps/extension/STORE_LISTING.md` (EN + BN).
- CI job: build + unit tests for the selector/phone extractor.

**Gate G5**: Chrome Web Store approval (operator).

---

## 8. Phase 6 — Official Meta path on

Branch `v4/phase-6`. No new tables.

**Must**
- When a Messenger/WhatsApp connection becomes `ACTIVE`, the server adds the matching
  packages to that shop's `covered_packages`; devices ignore them on next rules refresh;
  disconnect removes them.
- Remove "Coming soon" for Messenger/WhatsApp only when `META_APP_REVIEW_APPROVED=true`
  (config) **and** a connection health check passes.
- Chat-to-order drafts now flow into the same confirmation + Autopilot pipeline (they already
  share the draft table; verify with an end-to-end test).

---

## 9. Phase 7 — More reach

Branch `v4/phase-7`. Each WP is independent; each needs an operator-supplied contract.

- **WP7.1 SMS gateway**: real `SmsTransport` for a Bangladeshi gateway (contract in
  `docs/providers/sms/<provider>/`), sender id, segment cost from provider; enables SMS link and
  tracking SMS. Else `BLOCKED: SMS_PROVIDER_CONTRACT_REQUIRED`.
- **WP7.2 Carrybee + Paperfly adapters**: same adapter/manifest pattern as Steadfast. Else
  `BLOCKED: <COURIER>_CONTRACT_REQUIRED`.
- **WP7.3 Own API domain**: make every hard-coded `api.scalemyprints.com` configurable
  (`PUBLIC_BASE_URL`, `ECOMSBD_API_BASE_URL`, Meta callback docs, OAuth redirect docs), add a
  `docs/runbooks/API_DOMAIN_MIGRATION.md` for `api.ecomsbd.com` (dual-serve period, app
  min-version bump, Meta/Google redirect updates). Code must accept both hosts during migration.
- **WP7.4 Referral**: `referral_codes`, `referrals`; when a referred shop's first payment is
  approved, both shops get a 30-day plan credit via admin-manual grant (idempotent).

---

## 10. Ledger template — `docs/build/PROGRESS.md`

```markdown
# V4 build ledger
Spec: docs/V4_MASTER_BUILD_SPEC.md · Started: <date> · Branch: <current>

## Now
WP: <id> — <one line>   Step: <what is next, specific>

## Status
| WP | Status | Commit | Verified | Notes |
|---|---|---|---|---|
| WP0.1 | TODO | | | |
| WP0.2 | TODO | | | |
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
- <date> <WP>: <decision> — <why>

## Session log (newest first, one line each)
- <date> <WP> <what got done> <commit>
```

---

## 11. Operator checklist (Rabbi, in parallel — the build will stop at these)

- [ ] Steadfast merchant API key + secret → run smoke tool → tell Claude Code "Steadfast live verified".
- [ ] One real payout statement CSV per courier → `backend/tests/fixtures/statements/<provider>/` (phones masked).
- [ ] BD Courier (or other licensed) fraud-ratio API docs → `docs/providers/<provider>/`.
- [ ] Anthropic API key for the parser; run `python -m app.orders.parser.eval --live` once.
- [ ] Ask 2–3 BD voice providers: DTMF capture + webhook? per-call dynamic audio? caller ID? signature? price per call/min? → put the chosen one's docs in `docs/providers/voice/<provider>/`.
- [ ] Confirm with the voice provider whether BTRC has rules for automated transactional calls.
- [ ] BD SMS gateway docs + sender id.
- [ ] Meta App Review (`pages_messaging`, `pages_manage_metadata`) + Business Verification — start now.
- [ ] Play Console: closed testing track for the Notification Listener build; Data safety + privacy policy updated (notification reading, calls, SMS links, LLM processing).
- [ ] Sentry DSN (mobile).
- [ ] bKash/Nagad receiving numbers for `MANUAL_PAYMENT_ACCOUNTS`.
- [ ] 10 pilot sellers.
