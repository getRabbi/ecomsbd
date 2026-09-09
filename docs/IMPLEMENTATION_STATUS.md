# Implementation status

**Phase A (foundation) — complete.** Last updated 2026-09-09.

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
| **Tenant isolation** | Four central guards on the ORM session; 15 tests, incl. cross-tenant read/write/raw-SQL |
| Alembic | Portable migrations, `render_item` hook, URL from settings; chain + drift + reversibility tested |
| Money engine | Integer paisa, central half-up rounding, basis points, largest-remainder allocation, BD lakh formatting |
| Phone normalization | Bangla + Eastern-Arabic + full-width numerals, all E.164 shapes, multi-number extraction that never guesses |
| Time | UTC storage, Asia/Dhaka business dates, Friday-18:00 scheduling, `tzdata` pinned as a dependency |
| Durable outbox | Business row + event in one transaction, `FOR UPDATE SKIP LOCKED` claim, backoff, dead-letter parking, stale-lock recovery |
| Audit log | Append-only, actor/entity/reason/context, redacted before storage |
| Feature flags | Global / per-tenant / deterministic percentage rollout; provider flags default **off** |
| Idempotency | Key store with replay and body-mismatch conflict |
| Rate limiting | Fixed-window counters + distributed lock; Redis with an in-process fallback |
| Cache abstraction | Redis backend, in-process backend for local/test |
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

### API (`/v1`)

`POST /auth/otp/request` · `POST /auth/otp/verify` · `POST /auth/refresh` ·
`POST /auth/logout` · `POST /auth/select-tenant` · `GET /me` · `POST /tenants` ·
`GET /tenant` · `PATCH /tenant` · `GET /couriers/providers` ·
`GET /billing/entitlements` · `GET /billing/plans` · `GET /health/live` ·
`GET /health/ready`

### Flutter app

| Area | Detail |
|---|---|
| Branding | `ecomsbd` — Android label, `MaterialApp.title`, brand pill, menu title, search copy |
| Design tokens | Full palette, radii, spacing, shadows and type scale from the prototype CSS |
| Glass system | `GlassSurface` with a performant non-blur fallback, switched by a device-tier probe |
| Components | 26 reusable components (see below) |
| Charts | 6 chart types, all `CustomPainter`, zero dependencies |
| Local database | Drift with the offline mutation outbox, per-tenant cache, sync cursors; timestamps stored as UTC text |
| API client | Dio with bearer injection, **single-flight** token refresh, trace propagation, idempotency-key support, typed errors |
| Token storage | Android `EncryptedSharedPreferences`, never `SharedPreferences` or Drift |
| Auth state | Restore / sign-out / onboarding / ready, driving all routing |
| Router | `go_router` with redirection driven solely by auth stage |
| Screens | Splash, phone login, OTP verify, shop setup, main shell, Home, Orders, Money, Insights, Menu overlay |

**Components:** `EcomsbdScaffold`, `GlassTopPill`, `BrandPill`, `GlassIconButton`,
`GlassTopBar`, `GlassBackPill`, `GlassCard`, `StrongGlassCard`, `SectionHeader`,
`PageHeader`, `HeroMoneyCard`, `MetricTile`, `QuickActionTile`, `AttentionCard`,
`DarkHighlightPanel`, `SellerFeedCard`, `GlassListRow`, `RowIcon`, `OrderCard`,
`Timeline`, `FindingCard`, `MoneyAgingRow`, `PremiumChartCard`, `StatusChip`,
`RiskBadge`, `ProviderBadge`, `DataQualityBadge`, `MoneyText`,
`BottomGlassNavigation`, `GlassBottomSheet`, `OfflineBanner`,
`ProviderHealthBanner`, `EmptyState`, `SkeletonLoader`, `DashboardSkeleton`.

**Charts:** `ProfitTrendChart` (smooth gradient line + area), `CodDonutChart`,
`DeliveryFunnelChart`, `SettledVsDueChart` (grouped bars), `HorizontalBarChart`
(profitability and return pressure).

---

## Partially implemented

| Area | What exists | What is missing |
|---|---|---|
| **Home / Orders / Money / Insights screens** | Full layout, charts, components, responsive behaviour | Real data. They render fixtures from `lib/demo/` and each shows a visible **Demo data** marker. Wiring happens with the endpoints in Phases B–E. |
| Courier adapter | `CourierAdapter` Protocol, `BookingOutcome` incl. `UNKNOWN`, capability manifests, `Unavailable` result type | No provider implementation. No HTTP call exists anywhere in the codebase. |
| Provider manifests | Loader, three-valued capability state, four manifests | Steadfast/Pathao/RedX are entirely `unknown` pending real documentation |
| Entitlement metering | `require()` and limits work | `consume()` deliberately raises `NotImplementedError` — usage counters need the tables that own the metered actions (Phase B+). A silent no-op would ship a quota that is not enforced. |
| Subscriptions | State model, resolution, Free default | No verification path; only `manual_admin` can write a subscription |
| Offline outbox (client) | Drift tables, enqueue/claim/backoff/status, pending count | No sync engine yet — nothing sends the queue. `POST /v1/sync/mutations` is Phase B. |
| Offline detection | `isOfflineProvider` and the banner | Driven by API results only; no connectivity subscription yet |
| RBAC | Roles, permissions, matrix, `require_permission` dependency | Only Owner is reachable; no team-management endpoints |
| Notifications | `Notification` severity model in the outbox topics | No FCM, no in-app notification centre |

---

## Not started

Everything below is in the V1 spec and is scheduled, not dropped.

**Phase B — commerce core:** products, stock movements, customers, orders,
order items, `consignment_items`, manual order entry, paste-parse (deterministic
layer 1), CSV import framework, duplicate detection, sync protocol.

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
| Risk data source | A licensed provider or courier customer-stats capability | Risk check tile is visibly disabled rather than fake |

---

## Test coverage

| Suite | Count | Command |
|---|---:|---|
| Backend | 187 | `cd backend && pytest -q` |
| Flutter | 74 | `cd apps/mobile_flutter && flutter test` |

**Backend:** config and production guards (17), money rules (26), phone
normalization (32), tenant isolation (15), auth flow over HTTP (23), onboarding
and entitlements (14), platform primitives — outbox, idempotency, crypto,
redaction, flags, rate limiting, time, audit (45), migrations and schema
invariants (11).

**Flutter:** money formatting (13), components and accessibility (16), screens at
360dp with overflow assertions (12), local database (16), auth models and error
contract (17).

### Deliberately not covered yet

- No PostgreSQL-specific path is exercised by default (see ADR 0004). CI must
  run the backend suite against real PostgreSQL before any release.
- No golden financial scenarios yet — they arrive with the money engine in
  Phase D, where they belong.
- No integration test for the outbox worker loop end to end (needs Redis).
- No screenshot/golden tests for the UI.
