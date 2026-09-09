# Implementation status

**Phase B (commerce core) — COMPLETE.** Last updated 2026-09-10.

| | |
|---|---|
| **Phase A** | COMPLETE |
| **Phase B** | COMPLETE |
| **Canonical repository** | <https://github.com/getRabbi/ecomsbd.git> |
| **Branch** | `main` |
| **Phase A baseline commit** | `d70856f66ba206d1f1885235110680fbfc577eaf` |
| **Verified at Phase B** | 325 backend tests, 133 Flutter tests, ruff + ruff format + mypy --strict + flutter analyze + dart format all clean |

All Phase B–F work is committed to this repository. Workflow: implement a
vertical slice, test it, commit it, push it — not one commit at the end.

This file is the honest inventory. A feature is listed under **Implemented**
only when it works end to end and is covered by tests. An interface, a model or
a screen backed by fixtures is **Partially implemented** — master spec section
141: *"a beautiful screen backed by dummy data is not done."*

---

## Implemented

### Backend platform

| Area | Detail |
|---|---|
| Application bootstrap | FastAPI factory, lifespan, pure-ASGI middleware, `/v1` router |
| Configuration | Typed settings, four environments, **fail-fast** on placeholder secrets in staging/production |
| Structured logging | JSON lines with `trace_id` / `tenant_id` / `user_id`; redaction applied in the formatter |
| Redaction | Secrets removed, phone numbers masked (`01712****78`), bearer tokens scrubbed — in logs, audit rows and Sentry events |
| Error taxonomy | Stable machine codes + Bangla message + `retryable` + `reference_id`; 30 codes declared, incl. later-phase ones |
| Request context | contextvars for trace/tenant/actor, propagated through the pure-ASGI middleware |
| Database | Async SQLAlchemy 2, portable column types (`GUID`, `JSONColumn`, `TZDateTime`, `Paisa`) |
| **Tenant isolation** | Four central guards on the ORM session; 23 tests, incl. every Phase B table |
| Alembic | Portable migrations, `render_item` hook, URL from settings; chain + drift + reversibility tested |
| Money engine | Integer paisa, central half-up rounding, basis points, largest-remainder allocation, BD lakh formatting |
| Phone normalization | Bangla + Eastern-Arabic + full-width numerals, all E.164 shapes, multi-number extraction that never guesses |
| Time | UTC storage, Asia/Dhaka business dates, Friday-18:00 scheduling, `tzdata` pinned as a dependency |
| Durable outbox | Business row + event in one transaction, `FOR UPDATE SKIP LOCKED` claim, backoff, dead-letter parking, stale-lock recovery |
| Audit log | Append-only, actor/entity/reason/context, redacted before storage |
| Feature flags | Global / per-tenant / deterministic percentage rollout; provider flags default **off** |
| Idempotency | Key store with replay and body-mismatch conflict |
| Rate limiting | Fixed-window counters + distributed lock; Redis with an in-process fallback |
| Cursor pagination | `Page[T]`, opaque `(created_at, id)` cursor, `apply_cursor` written as an explicit OR for PostgreSQL/SQLite parity |
| Health | `/health/live`, `/health/ready` (DB + migration revision + Redis) |
| ARQ worker | Bootstrap, cron schedule, outbox dispatcher with per-tenant context and handler registry |
| Sentry | Optional; `send_default_pii=False` and a redaction `before_send` |

### Authentication

| Area | Detail |
|---|---|
| OTP challenge | 6 digits, 5-minute expiry, 5 attempts, HMAC-hashed and bound to its challenge id |
| Attempt counting | Increments **and commits** before comparison, so aborting a request costs an attempt |
| Rate limits | Per phone/hour, per IP/hour, resend cooldown |
| Sessions | Short JWT access token + opaque refresh token stored hashed |
| Token rotation | Single-use refresh with a `replaced_by` chain |
| **Reuse detection** | Presenting a rotated token revokes the whole session, commits the revocation, and audits it |
| Devices | Install-id keyed, platform/version/push token, revocable |
| Dev OTP provider | Three independent production guards; loud in-app warning |
| Logout | Single session or all devices |
| Shop switching | Server-verified membership, token re-issue |

### Tenancy, RBAC and entitlements

| Area | Detail |
|---|---|
| Shops | Create, read, update, onboarding step persisted server-side |
| Membership | `tenant_users` with roles; owner assigned on creation |
| RBAC | Five roles, 18 permissions, full matrix; enforced server-side |
| Entitlements | 13 keys, 3 plans, `require()` gate, snapshot endpoint, Free default |
| Subscriptions | State model and resolution; **no path grants a paid plan without verification** |

### Commerce core (Phase B)

| Area | Detail |
|---|---|
| Products | CRUD, SKU unique per shop, cost and selling price in paisa, low-stock threshold, **archive rather than delete** |
| Stock ledger | Append-only `stock_movements` with `balance_after`; the spec §10.4 reason set; stock is never set, only moved |
| Oversell guard | The balance update is one relative `SET stock = stock + :delta` statement with the non-negative condition inside it, so concurrent adjustments cannot lose each other or drift below zero without `allow_negative` |
| Customers | Phone-first identity; AES-GCM `phone_enc` + keyed HMAC + `phone_last4` + precomputed mask; denormalised order/delivered/returned counts; `success_rate_basis_points` is `null` with no terminal history, never 0 |
| Customer flags | `NONE` / `STARRED` / `BLOCKED` with a reason. Advisory only — nothing is enforced, and the wording is about the shop's decision, not the person (§130) |
| Addresses | `raw_address` is never overwritten by a courier's normalisation; provider location refs kept alongside |
| Phone reveal | One endpoint returns a plaintext number, requires a stated reason, and writes a `privacy.phone_revealed` audit entry |
| Orders | Full state machine (`DRAFT → CONFIRMED → PACKED → FULFILLMENT_STARTED → COMPLETED`, `CANCELLED` from the first three), address snapshot, `business_date`, `version` for conflict detection |
| Order numbering | `CP-YYYYMMDD-NNNN`, allocated with a savepoint retry on collision. The `CP-` prefix is a **stored value** and is deliberately not rebranded |
| Order items | Snapshot `product_name`, `sku`, `unit_price_paisa` and `unit_cost_snapshot_paisa` at creation, so editing a product later cannot move a past order's cost |
| **No auto-booking** | `POST /v1/orders` creates a record and contacts nobody. There is no courier HTTP call anywhere in the codebase |
| Paste parser (layer 1) | Deterministic, offline, no model. Extracts phones, name, address, items with size/colour, and the COD amount; a field it is unsure about comes back empty with a confidence score, and the original text is always echoed |
| Duplicate detection | 24-hour window, identity signals (same phone/customer) separated from corroborating ones (identical or similar amount, similar items); at least one corroboration is required, so a repeat customer is not flagged for existing. **Warns, never blocks** |
| Consignments | Full frozen §10.2 status machine incl. `BOOKING_UNKNOWN`, and `consignment_items` with a DB check that `delivered + returned <= shipped`. Schema only — no provider integration |
| Import framework | Upload → detect → dry-run → commit, for products and orders. SHA-256 refusal of a re-uploaded committed file, Bangla and English column aliases, per-row failure isolation, and **no silent coercion**: a bad phone or amount is reported with the value the file contained |
| Offline sync | `POST /v1/sync/mutations` idempotent by mutation id, `GET /v1/sync/changes` with tombstones; conflicts return the server's version and apply nothing. Only orders, products, customers and stock adjustments may be queued |

### API (`/v1`)

**Auth and account:** `POST /auth/otp/request` · `POST /auth/otp/verify` ·
`POST /auth/refresh` · `POST /auth/logout` · `POST /auth/select-tenant` ·
`GET /me` · `POST /tenants` · `GET /tenant` · `PATCH /tenant`

**Products:** `GET /products` · `POST /products` · `GET /products/{id}` ·
`PATCH /products/{id}` · `GET /products/{id}/stock-movements` ·
`POST /products/{id}/stock-adjustments`

**Customers:** `GET /customers` · `POST /customers` · `GET /customers/lookup` ·
`GET /customers/{id}` · `PATCH /customers/{id}` ·
`POST /customers/{id}/reveal-phone`

**Orders:** `GET /orders` · `POST /orders` · `POST /orders/parse` ·
`POST /orders/check-duplicates` · `GET /orders/{id}` · `PATCH /orders/{id}`

**Imports:** `POST /imports` · `POST /imports/{id}/dry-run` ·
`POST /imports/{id}/commit` · `GET /imports/{id}` · `GET /imports/{id}/rows`

**Sync:** `POST /sync/mutations` · `GET /sync/changes`

**Other:** `GET /couriers/providers` · `GET /billing/entitlements` ·
`GET /billing/plans` · `GET /health/live` · `GET /health/ready`

### Flutter app

| Area | Detail |
|---|---|
| Branding | `ecomsbd` — Android label, `MaterialApp.title`, brand pill, menu title, search copy |
| Design tokens | Full palette, radii, spacing, shadows and type scale from the prototype CSS |
| Glass system | `GlassSurface` with a performant non-blur fallback, switched by a device-tier probe |
| Components | 35 reusable components |
| Charts | 6 chart types, all `CustomPainter`, zero dependencies |
| Local database | Drift v2: offline outbox, per-tenant blob cache, sync cursors, and mirrors for products, customers and orders |
| **Real repositories** | Products, customers, orders and imports read through the API and mirror locally. No production repository can reach `lib/demo` |
| Cached reads | When the device is offline the mirror is served **labelled with when it was true**; when there is no mirror the screen says "No connection" rather than showing an empty list |
| Sync engine | Drains the outbox before pulling changes, treats `DUPLICATE` as success, backs off exponentially without losing an entry, parks conflicts, applies tombstones, and re-keys a local row to the server's id |
| Offline writes | Queued with the id the device minted, which is also the mutation id and the order's `client_id` — the same id on every retry |
| API client | Dio with bearer injection, **single-flight** token refresh, trace propagation, idempotency-key support, multipart upload, typed errors |
| Token storage | Android `EncryptedSharedPreferences`, never `SharedPreferences` or Drift |
| Screens | Splash, phone login, OTP verify, shop setup, main shell, Home, Orders, Money, Insights, Menu, **Products (list/form/stock adjustment/history), Customers (list/detail), Orders (feed/compose/detail), Imports** |

---

## Partially implemented

| Area | What exists | What is missing |
|---|---|---|
| **Home / Money / Insights screens** | Full layout, charts, components, responsive behaviour | Real data. They render fixtures from `lib/demo/` and each shows a visible **Demo data** marker. Wiring happens with the money and profit endpoints in Phases D–E. |
| Order card states | The server sends `fulfillment_state`, `risk_state` and `profit_state` explicitly, and the card renders them as **Not booked**, **Not checked** and **Pending** | The engines that would produce real values (Phases C, D, E). The placeholders are deliberate: a fabricated courier or profit value is worse than an honest gap. |
| Courier adapter | `CourierAdapter` Protocol, `BookingOutcome` incl. `UNKNOWN`, capability manifests, `Unavailable` result type, and now the `consignments` / `consignment_items` schema | No provider implementation. No HTTP call exists anywhere in the codebase. |
| Provider manifests | Loader, three-valued capability state, four manifests | Steadfast/Pathao/RedX are entirely `unknown` pending real documentation |
| Entitlement metering | `require()` and limits work | `consume()` deliberately raises `NotImplementedError` — usage counters need per-action tables. A silent no-op would ship a quota that is not enforced. |
| Subscriptions | State model, resolution, Free default | No verification path; only `manual_admin` can write a subscription |
| Offline detection | `isOfflineProvider`, the banner, and the sync controller that sets it from the last attempt | No connectivity subscription; the flag follows API results, which is what actually matters |
| Conflict resolution UI | A conflicted record is marked, kept, and never overwritten; the detail screen explains it | No side-by-side "yours / theirs" chooser yet |
| Import formats | CSV, with encoding fallbacks and delimiter sniffing | XLSX is detected and refused with a clear message rather than mis-parsed |
| RBAC | Roles, permissions, matrix, `require_permission` dependency | Only Owner is reachable; no team-management endpoints |
| Notifications | `Notification` severity model in the outbox topics | No FCM, no in-app notification centre |

---

## Not started

Everything below is in the V1 spec and is scheduled, not dropped.

**Phase C — Steadfast:** credential vault UI, validation, real booking, status
sync, webhooks, polling, `BOOKING_UNKNOWN` reconciliation, provider health.

**Phase D — money core:** COD receivables, financial ledger, payouts, payout
source-file preservation, payout adjustments, reconciliation scoring, mismatch
and dispute cases, COD aging, money endpoints.

**Phase E — profit and alerts:** charge snapshots, profit snapshots, return
economics, expenses and ad allocation, data-quality indicators, notification
centre, Friday summary, SMS.

**Phase F — billing and hardening:** Play Billing verification, bKash web
provider, admin/ops console, repair tooling, exports, backup verification,
release gates.

**V1.1 / V2:** Pathao, RedX, team UI, web dashboard, product variants, bulk
risk, courier scorecards, cashflow forecast, customer campaigns.

---

## Blocked / external verification required

Nothing below can be implemented from the specification alone. Each needs
something only the operator can supply. Master spec sections 61 and 140 forbid
inventing any of it.

| Item | Blocked on | Consequence today |
|---|---|---|
| **Android `applicationId`** | **An operator decision.** Neither the spec nor the prototype names one. Currently the scaffold default `com.example.ecomsbd`, which Play rejects — and the id is permanent once published. | `PACKAGE_ID_DECISION_REQUIRED` in `android/app/build.gradle.kts`. Blocks any Play upload and all billing work. |
| Release signing keystore | Operator | Release builds use the debug key |
| **Steadfast API** | Current merchant API documentation + a merchant account | Manifest is entirely `unknown`; no adapter. Manual courier mode is the working path. |
| Pathao API | Current merchant API documentation | Auto-address design decision is recorded and verified; endpoints are not |
| RedX API | Any documentation | Manifest placeholder only |
| bKash | Merchant onboarding contract | `BillingProvider` interface only |
| Google Play Billing | Package name + service account; policy re-check at release | No verification path. Section 90 forbids granting entitlement from a client callback. |
| SMS gateway | Provider choice, sender ID, encoding/segment cost rules | `SmsGatewayOtpProvider` raises rather than pretending to deliver |
| FCM | Firebase project credentials | Push token is stored; nothing sends |
| Cloudflare R2 | Bucket + keys | Needed before payout source-file preservation (Phase D) |
| Sentry | DSN | Integration is wired and inert without one |
| Risk data source | A licensed provider or courier customer-stats capability | Risk check tile is visibly disabled rather than fake; order cards read "Not checked" |

---

## Test coverage

| Suite | Count | Command |
|---|---:|---|
| Backend | 325 | `cd backend && pytest -q` |
| Flutter | 133 | `cd apps/mobile_flutter && flutter test` |

**Backend (325):** phone normalization (37), platform primitives — outbox,
idempotency, crypto, redaction, flags, rate limiting, time, audit (45), orders
and duplicate detection (35), imports and sync (29), money rules (26), products
and the stock ledger (25), auth flow over HTTP (23), tenant isolation (23),
parser (22), customers (19), config and production guards (16), onboarding and
entitlements (14), migrations and schema invariants (11).

**Flutter (133):** commerce screens (18), auth models and error contract (17),
components and accessibility (17), money formatting (16), commerce repositories
(16), local database (12), sync engine (10), Phase A screens at 360dp (10),
performance and large lists (9), commerce flows (8).

### Performance guards

Run as ordinary tests, so a regression fails CI rather than a device:

- a 30-row page off a 2,000-order local mirror, and the same page at offset 1,950
- search across 2,000 orders, and a low-stock filter across 500 products
- a full page of order cards and product rows at 360×800, and again at 412×915,
  with overflow assertions
- six keystrokes in the search box issuing one request, not six

### Deliberately not covered yet

- No PostgreSQL-specific path is exercised by default (see ADR 0004). CI must
  run the backend suite against real PostgreSQL before any release.
- No golden financial scenarios yet — they arrive with the money engine in
  Phase D, where they belong.
- No integration test for the outbox worker loop end to end (needs Redis).
- No screenshot/golden tests for the UI.
- No test drives a real device's file picker; the import screen takes its
  picker as a parameter and the tests supply a CSV directly.
