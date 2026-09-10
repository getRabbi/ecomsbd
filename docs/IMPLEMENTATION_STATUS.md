# Implementation status

**Phase E (profit and alerts) — COMPLETE.** Last updated 2026-09-10.

| | |
|---|---|
| **Phase A** | COMPLETE |
| **Phase B** | COMPLETE |
| **Phase C** | **BLOCKED** — needs Steadfast merchant API documentation and an account |
| **Phase D** | COMPLETE |
| **Phase E** | COMPLETE |
| **Canonical repository** | <https://github.com/getRabbi/ecomsbd.git> |
| **Branch** | `main` |
| **Phase A baseline commit** | `d70856f66ba206d1f1885235110680fbfc577eaf` |
| **Verified at Phase E** | 583 backend tests, 160 Flutter tests, ruff + ruff format + mypy --strict + flutter analyze + dart format all clean |

Phase D was built ahead of Phase C because Phase C cannot start: there is no
provider documentation to build against, and master spec section 140 forbids
inventing it. The money core depends on the order and consignment schema rather
than on a provider, so parcels move through **manual courier mode** — the seller
records a dispatch and later an outcome. When the Steadfast adapter arrives it
replaces that entry point and nothing downstream changes.

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
| **Tenant isolation** | Four central guards on the ORM session; 33 tests, incl. every Phase B, D and E table. A bulk `UPDATE` passes through none of the guards, so the one place that uses one filters by tenant explicitly and has its own regression test |
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

### Money core (Phase D)

| Area | Detail |
|---|---|
| **Financial ledger** | `financial_ledger_entries`, append-only (section 80). No update path exists anywhere in the application; a correction is a reversal row pointing at the original. The service is four public methods wide and a test asserts that set, so an `update` cannot appear later unnoticed. Amounts are non-negative and the direction carries the sign. |
| **COD receivables** | The full section 10.3 state machine, separate from both the consignment and the payout, because section 1.4 requires a parcel to be able to be DELIVERED while its money is unpaid. `EXPECTED` is deliberately *not* collectible — crediting at dispatch would show money for every parcel in transit. |
| Oversettlement guard | Section 17.2 as a database check constraint: `settled_paisa <= collectible_paisa + adjustment_paisa`. No code path can record more money arriving than was owed. |
| Partial delivery | Collectible is computed from the delivered units only (section 17.8); a partial without per-item quantities is refused rather than assuming the original COD. |
| Manual courier mode | Dispatch decrements stock and opens the receivable; an outcome sets the collectible amount, restores returned units and writes the ledger entries — all in one transaction. **No provider is contacted anywhere in the codebase.** |
| **Payouts** | `payouts`, `payout_lines`, `payout_source_files`, `payout_adjustments`. Recording money and applying it are separate steps: a statement is evidence, applying it is a decision. |
| Statement reader | Generic and marked `UNVERIFIED` — no provider format is confirmed. Detects columns in English and Bangla, suggests a mapping the seller corrects, and never coerces: an unreadable amount is reported with the file's own text, never imported as 0. |
| Idempotent import | Refused by content SHA-256 per shop (section 81.2). Two different shops may legitimately hold identical bytes. |
| Source preservation | The statement is kept (sections 81.4, 83) so support can explain a result later. `storage_key` is the R2 object it will live in; R2 is not provisioned, so the content sits in the row. |
| Deduction classification | Keyword rules; anything unrecognised stays `UNKNOWN_DEDUCTION` with the provider's own words attached (section 84). |
| **Reconciliation scoring** | Section 82's weights as data, asserted against the spec by a test. The auto-match threshold equals one exact reference, which makes "amount-only match → never auto-match" arithmetic rather than a special case. |
| Match decisions | One exact reference auto-matches. A tie, two parcels sharing a reference, or a soft-signal-only candidate goes to a human. An already-settled or returned parcel is rejected outright and kept in the candidate list so the seller sees it was considered. |
| **Shadow mode** | Section 112. Runs the whole engine and writes nothing — no settlement, no case, no ledger entry — so a rule can be measured before it is allowed to move money. |
| Manual match / unmatch | A manual match records who and why (section 81.7). Unmatching is a reversal, not a deletion (section 81.8): the original entries stay and two more undo them. |
| **Reconciliation cases** | All eight of section 16's cases as a separate issue entity, deduplicated so re-running the engine never grows the list. Closing one requires a note. |
| COD aging | Four bands in days, computed from `eligible_at`. Parcels in transit are excluded — nobody owes anything until delivery. |
| Manual correction | Section 134. States a change, never a new balance; requires a reason; writes a ledger entry with the actor attached. |
| **Golden scenarios** | All ten of section 54 are tests asserting exact figures, including the section 81.10 check that the outstanding total and the ledger's receivable bucket — derived separately — agree. |

### Profit and alerts (Phase E)

| Area | Detail |
|---|---|
| **Charge snapshots** | `consignment_charges`. A new charge supersedes the previous one of its kind rather than editing it, so a booking estimate replaced by a settled figure leaves both readable and a seller can see the courier charged more than it quoted. A weaker source cannot silently replace a stronger one (section 85); doing so needs a reason, which makes it section 17.3's audited correction. |
| **Profit snapshots** | `profit_snapshots`, one revision per change, never an edit (section 17.4). Written automatically when a parcel reaches a terminal outcome and again when a payout settles it — revenue moves BOOKED → SETTLED at that moment, which is the only way the quality indicator ever reaches "actual". A figure the seller read in March is still readable in December beside whatever replaced it. |
| Profit engine | Pure function, integer paisa, versioned by `CALCULATION_VERSION`. Discount is a memo, not a second subtraction — the order's revenue is already net of it. |
| **Data quality** | `ACTUAL` / `ESTIMATED` / `MISSING` derived from the worst source behind the figure, with essential inputs separated from peripheral ones: a missing ad cost makes a figure estimated, a missing delivery charge makes it incomplete. Section 135: an estimate never renders as exact. |
| **Return economics** | Section 19's full list — count, direct loss, outward and return legs, packaging, write-offs, rates by product, area and courier, and the reason. A sellable return is stock, not a loss; booking the returned goods as a cost would make every return look twice as expensive as it was. |
| Return reasons | Section 19's enum verbatim, in "risk" wording rather than defamatory person labels. Optional on the outcome sheet: an invented reason is worse for the report than a missing one, and the count with no reason is reported alongside the reasons. |
| **Expenses** | `expenses` + `expense_allocations`. Recording is not allocating (section 86) — an expense changes no profit figure until a named, versioned allocation runs. Ad spend allocates three ways; rent does not allocate at all, because section 86 warns against pretending fixed-cost allocation is accounting-grade. |
| Allocation arithmetic | Largest-remainder distribution, so the parts sum back to the exact expense. Spend that reached no parcel stays visible as unallocated rather than being smeared onto unrelated orders. Re-allocating requires a reason and writes new snapshot revisions. |
| **Alerts** | Section 23's four lines, derived from the reconciliation cases Phase D already opens rather than recomputed — two places deciding what "delivered but unpaid" means is two places that will eventually disagree. An alert with a count of zero is never raised. |
| **Notification centre** | `notifications`, deduplicated on `(tenant, kind, dedupe_key)`. Section 94: push is not enough, so everything lives in a table the seller can open. Without the constraint a daily scan would write a fresh "7 parcels unpaid" every morning until the seller stopped looking. |
| **Friday summary** | Section 23's whole weekly list, with section 24's sample rule enforced on both rankings. Below five parcels a product or courier ranking is withheld and replaced by a sentence saying why. The scheduled job runs hourly and decides for itself whether the *tenant's* clock has reached Friday 18:00, because ARQ fires crons on host local time. |
| COD arrival forecast | Dated from this shop's own observed median settlement lag per provider. Outstanding money with no such history is returned separately as unforecast rather than counted as due — section 140 forbids inventing provider payout timing, and a made-up date would send sellers chasing money that was never late. |

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

**Consignments (manual mode):** `POST /consignments/orders/{id}/dispatch` ·
`POST /consignments/{id}/outcome` · `GET /consignments/{id}` ·
`POST /consignments/{id}/charges` · `GET /consignments/{id}/charges`

**Money:** `GET /money/summary` · `GET /money/receivables` · `GET /money/aging` ·
`GET /money/receivables/{id}/ledger` ·
`POST /money/receivables/{id}/correction` ·
`POST /money/receivables/{id}/write-off` ·
`POST /money/receivables/{id}/dispute`

**Payouts:** `GET /payouts` · `POST /payouts/manual` · `POST /payouts/preview` ·
`POST /payouts/import` · `GET /payouts/{id}`

**Reconciliation:** `POST /reconciliation/payouts/{id}/reconcile` (`?shadow=true`) ·
`POST /reconciliation/lines/{id}/match` ·
`POST /reconciliation/lines/{id}/unmatch` · `POST /reconciliation/scan` ·
`GET /reconciliation/cases` · `PATCH /reconciliation/cases/{id}`

**Analytics:** `GET /analytics/home` · `GET /analytics/profit` ·
`GET /analytics/returns` · `GET /analytics/products` ·
`GET /analytics/weekly-summary`

**Expenses:** `GET /expenses` · `POST /expenses` · `GET /expenses/{id}` ·
`POST /expenses/{id}/allocate`

**Notifications:** `GET /notifications` · `GET /notifications/unread-count` ·
`POST /notifications/{id}/read` · `POST /notifications/read-all`

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
| **Real repositories** | Products, customers, orders, imports, money and analytics all read through the API. `lib/demo` is **deleted** — no screen has a fixture to fall back to |
| Cached reads | When the device is offline the mirror is served **labelled with when it was true**; when there is no mirror the screen says "No connection" rather than showing an empty list |
| Sync engine | Drains the outbox before pulling changes, treats `DUPLICATE` as success, backs off exponentially without losing an entry, parks conflicts, applies tombstones, and re-keys a local row to the server's id |
| Offline writes | Queued with the id the device minted, which is also the mutation id and the order's `client_id` — the same id on every retry |
| API client | Dio with bearer injection, **single-flight** token refresh, trace propagation, idempotency-key support, multipart upload, typed errors |
| Token storage | Android `EncryptedSharedPreferences`, never `SharedPreferences` or Drift |
| Screens | Splash, phone login, OTP verify, shop setup, main shell, Home, Insights, Menu, Products (list/form/stock adjustment/history), Customers (list/detail), Orders (feed/compose/detail/dispatch), Imports, **Money (summary/receivables/payouts/reconcile/cases)**, **Expenses**, **Notification centre** |
| Home and Insights | Every figure is a server value, and the locked design is unchanged. A failed load says so rather than rendering plausible numbers — the seller could not tell the difference. Section 24's sample rule is visible on every ranking, and COD with no settlement history behind it is labelled as having no arrival date instead of shown as due. |
| Expenses screen | Section 86's rule is the shape of the screen: every unallocated row says it has changed nothing yet, rent is not offered a per-parcel split at all, and re-allocating asks why before it moves a figure the seller has already read. |
| Notification centre | Everything the server raised, read or not, with the unread count on the shell's bell as a number rather than a dot. The Friday summary opens the figures it was *sent* with, so an old summary is not silently rewritten by today's data. |
| Money screens | Every figure is a server value. Outstanding and settled are kept visibly apart; an unexplained deduction is labelled as unexplained; a statement is previewed before import with unreadable rows shown as an em dash rather than a fabricated zero; reconcile is built around the engine's refusals, showing its reasoning in the seller's words. |

---

## Partially implemented

| Area | What exists | What is missing |
|---|---|---|
| Order card states | The server sends `fulfillment_state`, `risk_state` and `profit_state` explicitly, and the card renders them as **Not booked**, **Not checked** and **Pending** | Risk needs a data source (blocked). Profit and courier state are now real per parcel, but the order card still reads the order's own placeholder — wiring it to the consignment and its snapshot is a small follow-up. |
| Courier adapter | `CourierAdapter` Protocol, `BookingOutcome` incl. `UNKNOWN`, capability manifests, `Unavailable` result type, the `consignments` / `consignment_items` schema, and a working **manual** dispatch/outcome path | No provider implementation. No HTTP call exists anywhere in the codebase. Manual mode is the working path and is what the money core runs on. |
| Provider manifests | Loader, three-valued capability state, four manifests | Steadfast/Pathao/RedX are entirely `unknown` pending real documentation |
| Entitlement metering | `require()` and limits work | `consume()` deliberately raises `NotImplementedError` — usage counters need per-action tables. A silent no-op would ship a quota that is not enforced. |
| Subscriptions | State model, resolution, Free default | No verification path; only `manual_admin` can write a subscription |
| Offline detection | `isOfflineProvider`, the banner, and the sync controller that sets it from the last attempt | No connectivity subscription; the flag follows API results, which is what actually matters |
| Conflict resolution UI | A conflicted record is marked, kept, and never overwritten; the detail screen explains it | No side-by-side "yours / theirs" chooser yet |
| Import formats | CSV, with encoding fallbacks and delimiter sniffing, for both products/orders and courier statements | XLSX is detected and refused with a clear message rather than mis-parsed. No provider statement format is verified, so the statement reader is generic and the seller confirms the column mapping. |
| Payout source storage | The statement is retained in full, with its SHA-256 | Cloudflare R2 is not provisioned, so `storage_key` is null and the content lives in the database row. Moving it is a migration, not a redesign. |
| Provider payment API | `PayoutSource.API` exists in the model | No provider payment API is integrated. Statements and manual entry are the working paths. |
| RBAC | Roles, permissions, matrix, `require_permission` dependency | Only Owner is reachable; no team-management endpoints |
| Notifications | The in-app centre, severity model, deduplication, bundling, the four alerts and the Friday summary, all raised by scheduled jobs | No push transport. FCM is not provisioned, so `deserves_push` is computed and nothing sends. Section 94's rule that push is only ever a second copy means the product works without it. |
| Courier scorecard | Delivery success and return rate per courier from the shop's own history, with the sample rule visible | Section 24's median delivery time, effective cost per delivered parcel and settlement lag need per-provider timing data that only accumulates once real bookings exist |

---

## Not started

Everything below is in the V1 spec and is scheduled, not dropped.

**Phase C — Steadfast:** credential vault UI, validation, real booking, status
sync, webhooks, polling, `BOOKING_UNKNOWN` reconciliation, provider health.

**Remaining from Phase E:** SMS (a metered entitlement with no provider
integrated) and push transport. Everything else in the phase is complete.

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
| **Provider statement format** | One real Steadfast/Pathao/RedX payout statement | The statement reader is generic and marked `UNVERIFIED`. It works, and the seller confirms the column mapping, but no provider-specific profile can be written without a real file. |

---

## Test coverage

| Suite | Count | Command |
|---|---:|---|
| Backend | 583 | `cd backend && pytest -q` |
| Flutter | 160 | `cd apps/mobile_flutter && flutter test` |

**Backend (583):** platform primitives — outbox, idempotency, crypto,
redaction, flags, rate limiting, time, audit (45), reconciliation and the ten
golden financial scenarios (39), phone normalization (37), orders and duplicate
detection (35), analytics and expense endpoints over HTTP (34), tenant
isolation (33), alerts and the Friday summary (33), profit snapshots and
charges (29), payouts and statement parsing (29), imports and sync (29), COD
receivables and delivery outcomes (26), money rules (26), products and the
stock ledger (25), money endpoints over HTTP (24), auth flow over HTTP (23),
parser (22), customers (19), the financial ledger (17), expenses and ad
allocation (17), config and production guards (16), onboarding and
entitlements (14), migrations and schema invariants (11).

**Flutter (160):** Home, Insights, expenses and notifications (19), commerce
screens (18), auth models and error contract (17), components and
accessibility (17), money screens (17), money formatting (16), commerce
repositories (16), local database (12), sync engine (10), performance and
large lists (9), commerce flows (8), navigation (1).

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
- All ten golden financial scenarios are covered (section 54). What is *not*
  covered is a real provider statement: no courier's format has been verified,
  so the parsing tests use generic CSVs.
- No integration test for the outbox worker loop end to end (needs Redis).
- No screenshot/golden tests for the UI.
- No test drives a real device's file picker; the import screen takes its
  picker as a parameter and the tests supply a CSV directly.
