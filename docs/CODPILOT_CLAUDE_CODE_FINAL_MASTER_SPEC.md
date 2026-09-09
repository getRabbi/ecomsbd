# CODPilot — F-Commerce Seller Operations & COD Control System

> **FINAL CLAUDE CODE / CODEX BUILD CONTRACT** — Product scope, architecture, financial invariants, integration boundaries, tests, rollout and Definition of Done.

**Document type:** Final Product + Technical Master Specification  
**Status:** FROZEN V1 BUILD CONTRACT / single source of truth  
**Market:** Bangladesh-first  
**Primary client:** Android mobile app  
**Architecture style:** Modular monolith + adapter-based integrations  
**Version:** 2.0 — Claude-Code-ready final  
**Verification date for external assumptions:** 2026-09-09

---

## 0. Executive decision

**Working name:** `CODPilot`  
**Bangla-facing brand name:** keep undecided until seller interviews.

**One-line positioning (Bangla):**

> ফেসবুক পেজের অর্ডার থেকে কুরিয়ার বুকিং, COD-এর টাকা মিলানো আর আসল লাভ — সব এক অ্যাপে।

**English positioning:**

> The COD control panel for Bangladeshi Facebook sellers.

CODPilot is **not** a store builder, full ERP, accounting suite, payment processor, or delivery company.  
Its first job is:

> **Stop sellers from losing money on COD.**

The product becomes sticky by answering these questions every day:

1. আজ কত অর্ডার হলো?
2. কত পার্সেল delivered/returned?
3. courier-এর কাছে আমার কত টাকা আছে?
4. কোন delivered parcel-এর টাকা এখনও আসেনি?
5. কোথায় charge mismatch?
6. আজ/এই মাসে আসল profit কত?
7. কোন product/area/courier আমার profit নষ্ট করছে?

---

# 1. Product principles

## 1.1 Money must be visible every day

The home screen is not a generic analytics dashboard. It is the seller's **daily money control screen**.

Primary home metrics:

- Orders today
- Delivered today
- Returned today
- Gross sales
- Realized revenue
- Net/contribution profit
- COD outstanding
- COD expected today
- COD overdue
- Unmatched/mismatched settlement amount
- Return loss

## 1.2 Paid moat = reconciliation + accumulated history

Fraud/risk checking is an acquisition feature, not the defensibility layer.

The long-term moat is accumulated private seller history:

- courier-by-courier delivery success
- area-level return patterns
- product-level contribution profit
- COD aging history
- settlement reliability
- customer repeat/delivery history inside the seller's own business
- normalized order → parcel → settlement → profit graph

## 1.3 BYOC: Bring Your Own Courier account

Courier calls use the seller's own merchant credentials where supported.

Benefits:

- no courier resale margin or liability
- per-merchant rate limits
- cleaner ownership model
- seller keeps their negotiated courier relationship

**Fallbacks are mandatory:** if a provider has no usable API, the app still works through manual entry, CSV/statement import, or tracking-only mode.

## 1.4 Never mix operational state with financial state

A parcel can be `DELIVERED` while its COD is still unpaid.

Therefore CODPilot has separate state machines for:

- Order
- Consignment/delivery
- Return
- COD receivable/settlement
- Inventory movement
- Subscription entitlement

## 1.5 Deterministic money engine; AI only assists

AI may help with:

- paste-parse
- summaries
- later anomaly explanations

AI must **never** be the source of truth for:

- COD calculation
- settlement matching totals
- profit
- stock movement
- courier status
- billing entitlement

---

# 2. Final module map

| # | Module | V1 | V1.1 / V2 | Later |
|---|---|---|---|---|
| 1 | OTP onboarding + shop setup | ✅ | | |
| 2 | Courier account linking | ✅ Steadfast + manual | Pathao, RedX | Paperfly/eCourier/etc |
| 3 | Risk check | ✅ | Bulk + more providers | network intelligence |
| 4 | Orders | ✅ | API/import channels | Messenger ingestion |
| 5 | Paste-parse order | ✅ | smarter model/rules | page automation |
| 6 | Products + light inventory | ✅ | variants | purchase workflow |
| 7 | Courier booking | ✅ Steadfast | Pathao, RedX | more adapters |
| 8 | Status sync | ✅ | | |
| 9 | COD receivable ledger | ✅ | | |
| 10 | Payout import + reconciliation | ✅ | more auto-importers | |
| 11 | Money / COD aging screen | ✅ | cashflow forecast | |
| 12 | Profit engine | ✅ | ad allocation | advanced insights |
| 13 | Return loss center | ✅ | richer reasons | predictive alerts |
| 14 | Customer CRM | ✅ basic | campaigns | loyalty |
| 15 | SMS tracking notification | ✅ | templates | WhatsApp |
| 16 | Alerts + Friday summary | ✅ | smart alerts | |
| 17 | CSV import/export | ✅ | advanced mapping | |
| 18 | Team roles | | ✅ | |
| 19 | Web dashboard | | ✅ | |
| 20 | Subscription/billing | ✅ | | |
| 21 | Internal admin/ops console | ✅ minimal | advanced | |
| 22 | Courier scorecard | data collection | ✅ | recommendation engine |
| 23 | Cashflow forecast | | ✅ | |
| 24 | Meta ad integration | | optional | ✅ |
| 25 | Messenger/Page integration | | | ✅ |

---

# 3. Mobile information architecture

Bottom navigation:

1. **Home**
2. **Orders**
3. **Money**
4. **Insights**
5. **More**

## Home

Purpose: answer "business kemon cholche?" in under 10 seconds.

Suggested layout:

```text
আজ
Orders              46
Delivered           31
Returned              5
Sales             ৳54,200
Real Profit       ৳11,870

আমার টাকা
Courier-এর কাছে   ৳87,450
আজ পাওয়ার কথা     ৳21,300
Overdue             ৳8,750

Attention
🔴 3 delivered orders settlement হয়নি
🟠 5 returns → estimated loss ৳1,420
🟠 Black Abaya return rate increased
🟢 ৳12,340 settlement matched
```

## Orders

- All
- Draft
- Confirmed
- Packed
- Booked
- In transit
- Delivered
- Returned
- Cancelled
- COD pending filter
- Search by phone/name/order/tracking
- Courier filter
- Date filter

## Money

```text
COD Outstanding
৳87,450

Expected today
৳21,300

Overdue
৳8,750

Settled this month
৳2,84,500

Unmatched
৳4,240
```

Courier breakdown:

- Steadfast
- Pathao
- RedX
- Manual

COD aging:

```text
0–2 days       ৳45,200
3–5 days       ৳18,700
6–10 days       ৳8,900
10+ days        ৳4,650
```

## Insights

- Profit
- Returns
- Product profitability
- Courier scorecards
- Area performance
- Customer repeat behavior
- Ad profitability
- Later: cashflow forecast

## More

- Products
- Customers
- Expenses
- Courier accounts
- Imports/exports
- SMS
- Team
- Subscription
- Support
- Settings

---

# 4. Onboarding

Goal:

> install → shop created → first order → first booked consignment in under 10 minutes.

Flow:

1. Phone number
2. OTP
3. Shop name
4. Pickup address
5. Contact number
6. Business category
7. Link courier or continue manual mode
8. Optional product setup
9. First order walkthrough

### OTP rules

- 6-digit OTP recommended
- expiry: 5 minutes
- max attempts per OTP: 5
- per-phone + per-IP rate limits
- hashed OTP storage
- Android SMS Retriever where gateway/message format supports it
- never require email/password for normal seller login

---

# 5. Courier linking

Each provider is represented by a `CourierAdapter`.

Account screen:

```text
Steadfast    Connected ✓
Pathao       Not connected
RedX         Not connected
Manual       Enabled ✓
```

Rules:

- credentials are validated before activation
- raw credentials are never returned to the mobile client
- secret values are encrypted at rest
- provider capability is detected after validation
- account can be disabled without deleting historical parcels

A courier account can advertise capabilities:

```text
CREATE_SINGLE
CREATE_BULK
STATUS_LOOKUP
WEBHOOKS
BALANCE
PAYMENTS
RETURNS
PRICE_QUOTE
CUSTOMER_STATS
AUTO_ADDRESS
```

The app must not assume every courier supports every capability.

---

# 6. Risk check

## Purpose

Free acquisition hook + order confirmation aid.

Input:

- phone number

Output:

- total historical parcel count available from connected/approved sources
- success count
- cancel/return count
- provider breakdown
- order-risk label

Never label a person as "fraud".

Allowed output:

```text
Order risk: HIGH
Reason:
- 8 known deliveries
- 3 successful
- 5 cancelled/returned

Suggestion:
Advance delivery charge or phone confirmation recommended.
```

## Risk states

- `LOW`
- `MEDIUM`
- `HIGH`
- `UNKNOWN`

Initial heuristic:

- Low: strong success history
- Medium: mixed history
- High: materially poor history with enough sample size
- Unknown: no/insufficient data

The exact thresholds are configuration, not hard-coded business logic.

## Sources

Priority:

1. seller-linked courier account
2. licensed third-party API
3. no data

Never build central scraping of courier merchant panels as core architecture.

## Cache

Store normalized phone lookup results with:

- provider
- fetched_at
- expires_at
- raw totals
- source type

Default cache TTL: 24 hours.

---

# 7. Order creation

Three input paths:

## 7.1 Paste-parse

Seller pastes text copied from Messenger/WhatsApp.

Layer 1: deterministic parser:

- Bangla digit normalization
- BD phone normalization
- lines/colon parsing
- likely-name detection
- longest-address candidate
- amount/currency extraction
- size/color patterns
- product token matching

Layer 2: optional LLM fallback only if parser confidence is low.

Rules:

- LLM outputs JSON only
- schema validation required
- never auto-book
- seller must confirm/edit parsed fields
- per-plan quota
- log parse confidence, not sensitive prompt contents beyond operational retention policy

## 7.2 Manual quick form

Field priority:

1. phone
2. customer
3. address
4. product/items
5. amount
6. COD
7. note

Entering a valid phone should:

- load known customer
- show own-business delivery history
- optionally trigger paid auto-risk-check

## 7.3 CSV import

Required for migration from Google Sheets.

Importer must support:

- mapping UI
- preview
- validation errors by row
- duplicate detection
- dry-run
- final commit
- downloadable error report

---

# 8. Phone normalization

Canonical format:

```text
+8801XXXXXXXXX
```

Accept:

- `01712345678`
- `+8801712345678`
- `8801712345678`
- Bangla digits
- spaces
- hyphens

If multiple phone numbers are found, require explicit selection.

Never silently choose an ambiguous number.

---

# 9. Duplicate-order detection

Warn when:

- same tenant
- same normalized phone
- close COD amount
- within configurable time window
- order not cancelled

Initial window: 24 hours.

This is a warning, not a hard block.

---

# 10. State machines

## 10.1 Order state

```text
DRAFT
CONFIRMED
PACKED
FULFILLMENT_STARTED
COMPLETED
CANCELLED
```

Order is a commerce object; parcel status lives separately.

## 10.2 Consignment state

```text
NOT_BOOKED
BOOKING
BOOKING_UNKNOWN
BOOKED
PICKED_UP
IN_TRANSIT
OUT_FOR_DELIVERY
DELIVERED
PARTIAL_DELIVERED
RETURN_REQUESTED
RETURNING
RETURNED
CANCELLED
LOST
DAMAGED
FAILED
```

## 10.3 COD receivable state

```text
NOT_DUE
EXPECTED
ELIGIBLE
PAYOUT_IDENTIFIED
PARTIALLY_SETTLED
SETTLED
MISMATCHED
DISPUTED
WRITTEN_OFF
```

## 10.4 Inventory fulfillment state

Use stock movements, not a mutable "restocked" boolean.

Movement reasons:

```text
OPENING
MANUAL_ADJUSTMENT
BOOKED_RESERVE
BOOKED_DECREMENT
CANCEL_RESTORE
RETURN_RESTORE
PARTIAL_RETURN_RESTORE
DAMAGED_WRITE_OFF
```

---

# 11. Courier booking

## Core flow

```text
POST /orders/{order_id}/book
  ↓
validate entitlement + state
  ↓
create booking attempt + idempotency key
  ↓
create provisional consignment BOOKING
  ↓
provider adapter call
  ├ success → BOOKED
  ├ known 4xx → FAILED
  └ timeout/ambiguous → BOOKING_UNKNOWN
                       ↓
             reconciliation worker checks provider
```

A network timeout must never automatically imply "not booked".

## Idempotency

Server creates a booking attempt ID.

Recommended uniqueness:

```text
tenant_id + order_id + provider + booking_attempt_id
```

Provider-level merchant reference must be unique and traceable.

The server must return the existing attempt on retry when appropriate.

## Bulk booking

- chunk by provider limit
- isolate row failures
- no all-or-nothing transaction across provider HTTP calls
- each order keeps its own idempotency key

---

# 12. Pathao address strategy — corrected final decision

As of Pathao's July 23, 2025 merchant API update, API merchants can create an order with a sufficiently complete free-text recipient address and omit legacy city/zone/area fields; Pathao can auto-detect the delivery area.

Therefore:

**DO NOT make a custom fuzzy city/zone/area resolver a V1 hard dependency.**

Final design:

1. send complete normalized free-text address through provider's current supported auto-address flow
2. if provider responds with an address ambiguity/validation error:
   - ask user to improve/confirm address
   - if the account/API version requires structured IDs, use provider location endpoints/picker
3. optionally maintain local normalized area classification for analytics
4. later learn aliases for analytics/recommendations, not for blindly overriding courier routing

This saves a large amount of unnecessary V1 complexity.

---

# 13. Courier pricing engine

Courier charge cannot be treated as a global constant.

Sources of charge truth, in priority order:

1. provider price quote returned at booking/API
2. merchant-specific imported/configured rate card
3. platform rule pack
4. manual captured charge

Store a **snapshot** on each consignment.

Never recalculate old order profit using today's courier rate.

Suggested model:

```text
courier_rate_rules
- provider
- tenant_id nullable
- effective_from
- effective_to
- origin_zone
- destination_zone
- weight_min_grams
- weight_max_grams
- delivery_fee_paisa
- cod_percent_basis_points
- return_fee_paisa
- extra_weight_fee_paisa
- metadata_json
```

And per-consignment charge lines:

```text
consignment_charges
- consignment_id
- kind
- amount_paisa
- source
- provider_code
- captured_at
```

Kinds:

- delivery
- COD fee
- return
- weight
- insurance
- adjustment
- VAT/tax
- other

---

# 14. Status sync

Use both:

## 14.1 Webhooks

Endpoint:

```text
POST /v1/webhooks/courier/{provider}/{tenant_token}
```

Processing:

1. validate provider/tenant token/signature
2. persist raw event immediately
3. return quickly
4. enqueue normalization
5. update consignment idempotently
6. derive receivable changes
7. schedule notification if material

Do not perform expensive reconciliation inside webhook request.

## 14.2 Polling fallback

Suggested intervals:

- active/new parcel: every ~2h
- 1–7 days: every ~6h
- older unresolved: daily
- terminal + financially settled: stop

Intervals must be configurable per provider and rate limit.

---

# 15. COD receivable ledger

At booking/delivery, maintain a receivable object distinct from payout.

Suggested lifecycle:

```text
Order booked
→ EXPECTED

Delivered / partial delivered
→ ELIGIBLE

Payout imported
→ PAYOUT_IDENTIFIED

Matched full
→ SETTLED

Matched partial
→ PARTIALLY_SETTLED

Difference outside tolerance
→ MISMATCHED

Seller opens case
→ DISPUTED
```

For returned/cancelled parcels, receivable must be adjusted according to actual provider settlement rules.

---

# 16. Reconciliation engine

This is the primary paid feature.

## Input methods

1. Provider payment API
2. Courier CSV/statement import
3. Manual payout entry

## Matching priority

1. exact consignment ID
2. tracking code
3. merchant order/invoice reference
4. deterministic combination:
   - expected net amount
   - courier
   - delivery date window
   - phone
5. fuzzy/manual suggestion

Never silently auto-match a low-confidence fuzzy candidate.

## Confidence

```text
EXACT
HIGH
MEDIUM
MANUAL_REQUIRED
```

## Tolerance

Tolerance is configurable in paisa/BDT because providers may have small adjustments.

## Reconciliation cases

Create a separate issue entity for:

- delivered but unpaid
- underpaid
- overpaid
- unknown deduction
- duplicate payout line
- payout cannot be mapped
- stale in-transit
- returned but inventory not restored

---

# 17. Money invariants

These must be enforced by tests.

1. One provider payout line cannot settle more than its own amount.
2. A consignment can have many settlement lines, but settled total cannot exceed collectible net without an explicit adjustment.
3. Historical charge snapshots are immutable except via audited correction.
4. Profit snapshot changes require a new revision/audit event.
5. Money uses integer paisa.
6. Currency is explicitly `BDT`.
7. Rounding rule is centralized.
8. Partial delivery creates a partial collectible amount, never assumes original COD.
9. Return charges can create negative profit even with zero collected revenue.
10. No provider event directly modifies money totals without going through the domain service.

---

# 18. Profit engine

## 18.1 Per-order contribution profit

```text
Realized Revenue
- Product Cost
- Courier Charges
- COD Fee
- Packaging
- Discount
- Payment Fee
- Allocated Ad Cost
- Other Variable Cost
= Contribution Profit
```

Optional fixed-cost view:

```text
Contribution Profit
- Allocated Fixed Expenses
= Estimated Net Business Profit
```

Do not pretend fixed-cost allocation is accounting-grade unless methodology is explicit.

## 18.2 Snapshot model

At financial settlement, create a profit snapshot containing:

- realized revenue
- item cost
- courier charge components
- packaging
- ad allocation
- other variable cost
- contribution profit
- margin basis points
- calculation version

Never silently alter old snapshots when configuration changes.

---

# 19. Return economics

Track:

- return count
- direct return loss
- outward delivery cost
- return delivery cost
- packaging loss
- damaged/write-off inventory
- product return rate
- area return rate
- courier return rate
- return reason

Initial reason enum:

- customer unreachable
- customer refused
- wrong product
- wrong size
- damaged
- delayed delivery
- changed mind
- fake/invalid order suspicion
- courier issue
- merchant issue
- other

Use "risk" wording, not defamatory person labels.

---

# 20. Product and inventory

V1:

- product
- SKU
- cost
- selling price
- stock
- image
- active/inactive
- optional free-text order item

Stock rules:

- configurable reserve/decrement moment
- default: decrement on successful booking
- cancel before pickup → restore
- full return → restore if sellable
- damaged return → write-off
- partial delivery → item-level adjustment required

V1 must not build:

- suppliers
- purchase orders
- multi-warehouse
- batch accounting

---

# 21. Customer CRM

Customer is private to one tenant.

Fields/derived stats:

- normalized phone
- name
- default addresses
- order count
- delivered count
- returned/cancelled count
- lifetime realized revenue
- repeat-buyer badge
- seller-private notes
- seller-private block/star
- own-business success rate

No cross-seller customer browsing.

---

# 22. SMS

Transactional SMS events:

- booking confirmation
- tracking information
- optional delivery confirmation later

Prefer concise GSM-7/Banglish templates where practical because Unicode segmentation is smaller than GSM-7.

Store:

- template
- recipient
- provider
- segment count
- cost
- status
- provider message ID
- failure reason

SMS is a metered entitlement; include quota + top-ups.

---

# 23. Alerts and retention loop

High-value alerts:

```text
🔴 Delivered but unpaid: 7 parcels — ৳8,950
🟠 Underpaid: 3 — shortage ৳420
🟡 15+ days in transit: 4 — ৳5,200 exposed
🔵 Returned but stock not restored: 6
```

Daily:

- COD due
- meaningful mismatch
- stalled parcel
- unusual return spike

Weekly Friday summary:

- orders
- delivered
- returns
- return loss
- sales
- contribution profit
- COD outstanding
- overdue
- mismatch count
- best/worst product
- best/worst courier from seller's own data when sample is sufficient

Do not send noisy notifications for non-actionable changes.

---

# 24. Courier scorecard

Do not show unreliable rankings before enough sample exists.

Metrics:

- delivered / attempted
- return rate
- median delivery time
- effective cost per delivered parcel
- average settlement lag
- mismatch frequency
- area-specific performance

Sample-size rule must be visible.

Example:

```text
Steadfast — Mirpur
42 completed parcels
Delivery success: 90.5%
Median delivery: 1.9d
Effective cost/delivered: ৳86
Settlement lag: 2.1d
```

Later recommendation engine:

> Best courier for this order

based only on seller's own history + sufficient sample, then optional anonymized market benchmark if legally/privacy-safe.

---

# 25. Cashflow forecast (V2)

Forecast:

- expected COD inflows
- known supplier/fixed expenses entered by seller
- ad budget
- estimated free cash

Keep it a forecast, never an accounting guarantee.

---

# 26. Subscription plans

Initial hypothesis:

| Feature | Free | Starter ৳199 | Pro ৳399 |
|---|---:|---:|---:|
| Orders/month | 20 | Unlimited | Unlimited |
| Risk checks/day | 10 | 100 | High/unlimited policy |
| Auto risk on order | ❌ | ✅ | ✅ |
| Courier accounts | 1 | 3 | higher/unlimited |
| Bulk booking | ❌ | ✅ | ✅ |
| COD reconciliation | limited/view | ✅ | ✅ |
| Profit history | today | full | advanced |
| SMS included | 0 | configured quota | larger quota |
| AI paste parse | 5/mo | 100/mo | 500/mo |
| Team | 1 | 1 | 5 |
| Web dashboard | ❌ | ❌ | ✅ |
| CSV export | ❌ | ✅ | ✅ |

Alternative packs to test:

- quarterly
- yearly

**Do not lock seller-owned historical data.**  
Gate volume, automation, collaboration, advanced analysis, and quotas.

---

# 27. Billing architecture — corrected for Google Play

There are two billing channels.

## 27.1 Play-distributed Android build

For subscriptions/features sold **inside** a Google Play-distributed app, implement Google Play Billing unless the app qualifies for an applicable policy exception/program.

Bangladesh is not currently listed among the countries in Google's alternative-billing country list referenced in the verified policy sources used for this specification.

Therefore:

- Play build must not contain an in-app "Pay with bKash" external link for digital subscription as a default strategy.
- Play users can buy via Play Billing.
- Existing users who bought legitimately outside the app may sign in and consume their entitlement if the app is structured as a consumption app; no prohibited in-app steering.
- Re-check policy immediately before release.

## 27.2 Web / PWA / direct distribution

Use bKash merchant/tokenized/subscription products where merchant onboarding grants access.

Architecture:

- server-side bKash client
- token cache
- agreement/payment references encrypted where sensitive
- invoice records
- webhook/IPN verification
- dunning
- entitlement activation only after verified payment

## 27.3 Unified entitlement service

The mobile app never trusts a local "paid=true".

Server returns:

```json
{
  "plan": "starter",
  "status": "active",
  "source": "play|bkash_web|manual_admin",
  "valid_until": "...",
  "entitlements": [...]
}
```

All feature checks occur server-side for sensitive/paid operations.

---

# 28. Recommended technical architecture

```text
┌──────────────────────────────────────────────┐
│ Flutter Android App                         │
│ Riverpod + Drift + offline mutation queue   │
└────────────────────┬─────────────────────────┘
                     │ HTTPS / JSON
                     ▼
┌──────────────────────────────────────────────┐
│ FastAPI modular monolith                    │
│ Auth | Tenant | Orders | Courier | Money    │
│ Risk | Profit | Inventory | Billing | Admin │
└──────────┬───────────────┬───────────────────┘
           │               │
           ▼               ▼
      PostgreSQL       Redis / ARQ
      Supabase         jobs/cache/locks
           │               │
           └──────┬────────┘
                  ▼
             External Adapters
        ┌─────────┼───────────┐
        ▼         ▼           ▼
     Courier    SMS        Billing
     APIs       gateway    Play/bKash web

Object storage: Cloudflare R2
Push: FCM
Errors: Sentry
Deploy: Docker Compose on one VPS initially
```

---

# 29. Stack decisions

| Layer | Choice |
|---|---|
| Mobile | Flutter |
| State | Riverpod |
| Local DB | Drift / SQLite |
| Backend | FastAPI, Python 3.12 |
| Validation | Pydantic |
| ORM | SQLAlchemy 2 async |
| Migrations | Alembic |
| DB | PostgreSQL |
| Managed DB | Supabase Postgres |
| Queue/cache | Redis |
| Worker | ARQ |
| Storage | Cloudflare R2 |
| Push | FCM |
| Error tracking | Sentry |
| Infra | Docker Compose, single VPS first |
| V2 web | Next.js |

Supabase is the managed Postgres layer, not the mobile app's trusted business API.

---

# 30. Backend bounded modules

Suggested package layout:

```text
app/
  auth/
  tenants/
  users/
  couriers/
    adapters/
    domain/
    webhooks/
  orders/
  customers/
  products/
  inventory/
  risk/
  money/
    receivables/
    payouts/
    reconciliation/
  profit/
  expenses/
  notifications/
  sms/
  billing/
  sync/
  admin/
  common/
    money/
    security/
    idempotency/
    events/
    feature_flags/
```

Do not create microservices in V1.

---

# 31. Final data model

Every tenant-owned table includes:

- `tenant_id`
- `created_at`
- `updated_at`
- soft-delete where appropriate

Core:

```text
tenants
users
tenant_users

courier_accounts
courier_account_capabilities
courier_rate_rules

products
product_variants (optional V1.1)
stock_movements

customers
customer_addresses

orders
order_items

booking_attempts
consignments
consignment_charges
courier_events

cod_receivables
payouts
payout_lines
reconciliation_cases

expenses
ad_spend_entries

profit_snapshots

risk_checks
risk_provider_results
risk_check_usage

sms_messages
notifications

subscriptions
subscription_entitlements
billing_customers
billing_invoices
billing_attempts

imports
import_rows
exports

sync_changes
device_sync_cursors

outbox
audit_logs
feature_flags
provider_health
```

---

# 32. Money storage

Use integer paisa:

```text
amount_paisa BIGINT
```

Helpers:

```text
Money(BDT, paisa)
```

Never use float for business money.

Percentages:

- basis points (`100 = 1%`) or Decimal
- central rounding helper
- explicit rounding test cases

---

# 33. PII strategy

Phones are highly sensitive.

Recommended:

- canonical normalized phone for application logic
- protected searchable representation for exact lookup
- encrypted display value at rest if feasible
- strict log redaction
- bulk export audit
- rate-limited exports

Never log:

- OTP
- full courier credential
- bKash secret
- Play billing secret
- customer phone in plain text request logs
- access/refresh token

---

# 34. Courier adapter contract

```python
class CourierAdapter(Protocol):
    provider: str

    async def validate_credentials(self, creds) -> ValidationResult: ...
    async def capabilities(self, creds) -> set[str]: ...
    async def list_stores(self, creds) -> list[Store]: ...
    async def quote(self, creds, req: QuoteRequest) -> Quote | None: ...

    async def create_consignment(
        self,
        creds,
        req: BookingRequest,
        merchant_reference: str,
    ) -> ProviderConsignment: ...

    async def create_bulk(
        self,
        creds,
        reqs: list[BookingRequest],
    ) -> list[ProviderBookingResult]: ...

    async def get_status(self, creds, ref) -> ProviderStatus: ...
    async def cancel(self, creds, ref) -> CancelResult: ...
    async def request_return(self, creds, ref, reason) -> ReturnResult | None: ...

    async def get_balance(self, creds) -> Money | None: ...
    async def list_payouts(self, creds, since) -> list[ProviderPayout] | None: ...
    async def customer_stats(self, creds, phone) -> ProviderCustomerStats | None: ...

    def verify_webhook(self, headers, body: bytes) -> bool: ...
    def parse_webhook(self, headers, body: bytes) -> list[ProviderEvent]: ...
```

Every optional capability returns unavailable rather than throwing "not implemented".

---

# 35. Adapter wrapper

Implemented once:

- credential decrypt
- OAuth token cache
- Redis distributed lock for token refresh
- timeouts
- retry policy
- circuit breaker
- provider request IDs
- rate limiting
- redacted logs
- provider health metrics

Rules:

- safe GET/read calls may retry
- create/mutate calls only retry through explicit idempotent/reconciliation strategy
- ambiguous timeout → `BOOKING_UNKNOWN`

---

# 36. Booking unknown reconciliation

Worker algorithm:

```text
booking attempt timed out
→ mark BOOKING_UNKNOWN
→ do not let app blindly create another parcel
→ query provider using merchant reference/invoice
→ if found: attach provider ID + BOOKED
→ if definitely absent: allow controlled retry/new attempt
→ if unresolved: support queue/manual review
```

This is a P0 money-safety requirement.

---

# 37. Offline-first mobile

Offline allowed:

- create/edit unbooked orders
- products
- customers
- notes
- mark packed
- view cached data

Network required:

- courier booking
- risk lookup
- provider status refresh
- payout import
- subscription checkout
- server analytics refresh

Local outbox:

```text
id
entity_type
entity_id
operation
payload
client_ts
status
attempts
```

Client IDs use UUIDv7/UUID.

Conflict rules:

- courier-derived fields: server authoritative
- payment fields: server authoritative
- seller-editable pre-booking fields: field/version based or last-write-wins
- after booking, critical shipping fields require explicit correction workflow

Do not queue a courier booking silently as if it succeeded. UI must clearly show network requirement/pending state.

---

# 38. Sync protocol

Recommended:

```text
POST /v1/sync/mutations
GET  /v1/sync/changes?cursor=...
```

Server returns:

- accepted mutations
- conflicts
- changed entities
- next cursor

Use tombstones for deletions.

---

# 39. Core API surface

## Auth

```text
POST /v1/auth/otp/request
POST /v1/auth/otp/verify
POST /v1/auth/refresh
POST /v1/auth/logout
```

## Tenant

```text
GET  /v1/me
GET  /v1/tenant
PATCH /v1/tenant
```

## Couriers

```text
GET    /v1/couriers/providers
GET    /v1/couriers/accounts
POST   /v1/couriers/accounts
POST   /v1/couriers/accounts/{id}/validate
DELETE /v1/couriers/accounts/{id}
POST   /v1/couriers/quote
```

## Orders

```text
GET  /v1/orders
POST /v1/orders
GET  /v1/orders/{id}
PATCH /v1/orders/{id}
POST /v1/orders/{id}/confirm
POST /v1/orders/{id}/pack
POST /v1/orders/{id}/book
POST /v1/orders/bulk-book
POST /v1/orders/parse
```

## Risk

```text
POST /v1/risk/check
POST /v1/risk/bulk-check
```

## Money

```text
GET  /v1/money/summary
GET  /v1/money/receivables
GET  /v1/money/aging
GET  /v1/payouts
POST /v1/payouts/manual
POST /v1/payouts/import
POST /v1/payouts/{id}/reconcile
GET  /v1/reconciliation/cases
PATCH /v1/reconciliation/cases/{id}
```

## Analytics

```text
GET /v1/analytics/home
GET /v1/analytics/profit
GET /v1/analytics/returns
GET /v1/analytics/products
GET /v1/analytics/couriers
GET /v1/analytics/areas
```

## Products/customers/expenses

Standard CRUD + audited export endpoints.

## Billing

```text
GET  /v1/billing/entitlements
GET  /v1/billing/plans
POST /v1/billing/play/verify
POST /v1/billing/web/checkout
POST /v1/billing/webhooks/{provider}
```

The Play build must expose only policy-compliant purchase flows.

---

# 40. Webhook architecture

Courier webhook:

```text
Internet
→ ingress endpoint
→ signature/token verification
→ raw event insert
→ 200 response
→ outbox/job
→ normalize
→ state transition
→ money domain update
→ notification
```

Billing webhook follows the same durable-ingress pattern.

---

# 41. Outbox pattern

Any domain transaction that must trigger external work writes:

1. business row
2. outbox event

in the same DB transaction.

Examples:

- order booked → SMS
- payout imported → reconcile
- COD overdue → push
- subscription renewed → entitlement refresh

Worker claims with `FOR UPDATE SKIP LOCKED` or equivalent safe queue semantics.

---

# 42. Background jobs

| Job | Suggested cadence |
|---|---|
| active consignment polling | tiered |
| unknown booking reconciliation | every 10 min |
| provider payout import | daily/provider dependent |
| payout matching | event-driven |
| COD aging | daily |
| Friday summary | Fri 18:00 Asia/Dhaka |
| area/provider reference sync | provider-dependent |
| risk cache cleanup | hourly |
| SMS send | continuous |
| subscription dunning | daily |
| export cleanup | daily |
| provider health check | frequent lightweight |
| backup verification | daily |
| analytics aggregation | nightly + incremental |

---

# 43. Subscription entitlement architecture

Never scatter `if plan == "pro"` across code.

Create entitlements:

```text
orders_monthly_limit
risk_checks_daily
auto_risk
courier_account_limit
bulk_booking
reconciliation
profit_history_days
advanced_profit
sms_segments_monthly
ai_parse_monthly
team_member_limit
web_dashboard
csv_export
```

Function:

```python
await entitlements.require(tenant_id, "bulk_booking")
```

Usage counters are server authoritative.

---

# 44. Internal admin/ops console — V1 required

This is not a seller-facing module, but operationally essential.

Admin can:

- search tenant
- see subscription/entitlements
- see provider account health without seeing secrets
- see unknown bookings
- replay failed webhook processing
- inspect redacted provider call trace
- see payout/reconciliation failures
- see SMS failures/cost
- disable a broken provider feature via feature flag
- issue support credit/plan extension with audit trail
- export support bundle with redaction

Admin cannot casually browse customer PII.

Every sensitive admin action is audited.

---

# 45. Feature flags

Examples:

```text
pathao_enabled
redx_enabled
provider_auto_address
risk_aggregator_fallback
ai_parse_enabled
bkash_web_billing_enabled
play_billing_enabled
courier_recommendation_enabled
```

Flags can be:

- global
- per tenant
- percentage rollout

Provider outage should be survivable without releasing a new app version.

---

# 46. Error taxonomy

Return stable machine codes + Bangla user message.

Example:

```json
{
  "code": "COURIER_PROVIDER_UNAVAILABLE",
  "message_bn": "কুরিয়ার সার্ভার এখন সাড়া দিচ্ছে না। অর্ডারটি আবার বুক করবেন না—আমরা আগে আগের চেষ্টা যাচাই করছি।",
  "retryable": true,
  "reference_id": "..."
}
```

Important codes:

- `BOOKING_AMBIGUOUS`
- `INVALID_COURIER_CREDENTIALS`
- `ADDRESS_REJECTED`
- `ORDER_NOT_BOOKABLE`
- `ENTITLEMENT_REQUIRED`
- `RISK_PROVIDER_UNAVAILABLE`
- `PAYOUT_IMPORT_INVALID`
- `RECONCILIATION_MISMATCH`
- `SYNC_CONFLICT`

---

# 47. Security

## Auth

- short access token
- rotating refresh token
- device/session tracking
- refresh token hashed at rest
- revoke sessions

## Credential vault

Initial:

- AES-GCM application encryption
- versioned key ID
- key from protected environment/secret manager

Later:

- managed KMS

Credential plaintext lifetime should be minimal in process memory.

## Tenant isolation

Use a mandatory tenant-scoped repository/session pattern.

Integration test:

> tenant A can never retrieve tenant B resource by ID.

Test every endpoint category.

## Rate limiting

Per:

- IP
- user
- tenant
- courier account
- risk phone lookup
- OTP
- export
- AI parse

## Logging

Structured logs with:

- trace_id
- tenant_id
- provider
- operation
- duration
- result
- redacted error

No secrets/PII.

---

# 48. Backup and disaster recovery

Minimum:

- managed Postgres backups
- daily logical backup where practical
- R2 lifecycle policy
- encrypted secrets backup strategy
- monthly restore drill minimum; automated verification preferred
- documented RPO/RTO targets

Do not claim backup safety unless restore has been tested.

---

# 49. Observability

Metrics:

- API latency/error rate
- booking success
- booking unknown count
- provider error rate
- webhook lag
- polling backlog
- reconciliation match rate
- payout unmatched amount
- SMS failure rate
- OTP cost/rate
- active tenants
- subscription renewals
- entitlement denials
- offline sync conflicts

Sentry:

- mobile
- backend

Provider dashboard:

- healthy
- degraded
- down
- disabled

---

# 50. Analytics definitions

Do not mix business definitions.

Examples:

### Delivery success rate

```text
delivered_or_partial / terminal_attempts
```

Definition must be versioned/documented.

### Return rate

Choose denominator explicitly:

- booked
- picked up
- terminal

Use one standard and label it.

### Realized revenue

Revenue actually collected/settled, not merely ordered amount.

### COD outstanding

Eligible collectible amount minus settled amount, adjusted for verified deductions.

### Profit

Always label:

- gross profit
- contribution profit
- estimated net profit

---

# 51. Data retention and portability

Seller owns access to their business data.

Provide:

- CSV export
- account export later
- soft delete retention policy
- clear deletion workflow
- no historical data hostage behavior after downgrade

A downgraded user can read their history; automation/volume/advanced analysis can be restricted.

---

# 52. V1 UX requirements

- Bangla-first labels
- Banglish acceptable in SMS
- large tap targets
- numeric keyboard for money/phone
- low-bandwidth loading
- skeletons, not spinner forever
- explicit offline state
- explicit provider-down state
- no hidden retry that could duplicate booking
- call customer button
- copy phone/tracking
- WhatsApp/support button
- empty states explain next action

---

# 53. Low-end Android requirements

Targets:

- Android-first
- keep app lean
- no autoplay media
- image compression before upload
- paginated lists
- local cache
- avoid huge JSON payloads
- test on low-memory physical device
- monitor cold start and DB migration time

---

# 54. Test strategy

## Unit tests

Mandatory:

- phone normalization
- money rounding
- profit calculation
- COD eligibility
- partial delivery
- payout matching
- charge snapshots
- state transitions
- entitlement rules
- risk banding
- address normalization helpers
- duplicate detection

## Adapter contract tests

Every adapter must pass a shared test suite:

- invalid credentials
- timeout
- 4xx
- 5xx
- create success
- ambiguous create
- status mapping
- webhook duplicate
- malformed payload
- unsupported capability

## Integration tests

- tenant isolation
- DB transaction + outbox
- booking idempotency
- webhook idempotency
- payout import + match
- offline mutation sync
- billing entitlement verification

## Golden financial scenarios

Keep fixtures for:

1. normal delivered COD
2. delivery + COD fee
3. return
4. partial delivery
5. underpayment
6. overpayment
7. split payout
8. one payout covering many parcels
9. duplicate payout line
10. historical rate-card change

A release must not change expected totals without explicit migration/versioning.

---

# 55. P0 acceptance criteria

## Onboarding

- new seller can create account + shop
- can skip courier linking
- linked credential validates before save

## Order

- offline order creation works
- Bangladesh phone formats normalize
- duplicate warning works
- paste parse always confirms before save/book

## Booking

- one real Steadfast parcel can be booked
- retry cannot create accidental duplicate in our system
- ambiguous timeout enters `BOOKING_UNKNOWN`
- unknown reconciliation resolves or escalates

## Status

- duplicate webhook doesn't duplicate state event
- polling can recover missed webhook
- raw provider status retained

## Money

- delivered parcel creates correct eligible COD receivable
- payout API/CSV/manual path can create payout
- exact match reconciles
- mismatch creates case
- COD aging correct

## Profit

- product cost + courier charge + COD fee + packaging + expense allocation calculated in paisa
- return loss correct
- old order profit does not change after new rate rule

## Offline

- local orders sync without duplication
- server financial/provider fields win conflicts

## Billing

- server entitlement required for paid action
- payment provider webhook/verification required before activation
- downgrade never deletes data

---

# 56. Build phases

## Phase 0 — concierge validation

Before heavy code:

- 20 sellers
- manually reproduce order → courier → payout → reconciliation
- collect sample courier statements
- learn exact payout pain
- test ৳199 willingness-to-pay

Suggested validation gate:

- strong qualitative pain
- at least meaningful number willing to pay
- ideally real payments, not only verbal interest

Do not fake statistical certainty from 20 sellers.

## Phase 1 — foundation

- FastAPI
- Postgres
- tenancy
- OTP
- Docker
- Flutter shell
- Drift
- sync

## Phase 2 — commerce core

- products
- customers
- orders
- paste parse
- CSV import

## Phase 3 — Steadfast

- credentials
- booking
- status
- balance/payment capability where merchant API supports it
- webhook
- unknown reconciliation

## Phase 4 — money core

- receivables
- payout import
- matching
- COD aging
- four alerts
- Money screen

## Phase 5 — profit

- P&L
- expenses
- return economics
- dashboard

## Phase 6 — billing + hardening

- entitlements
- Play Billing path
- bKash web path
- Sentry
- backups
- admin console
- restore drill

## Phase 7 — Pathao

Use current auto-address API flow first.

Add structured-area fallback only if required by actual merchant account/API behavior.

## Phase 8 — RedX + scale features

- adapter
- team
- web
- advanced imports
- courier scorecards

---

# 57. V1 non-goals

Do not build:

- storefront
- website builder
- full accounting
- VAT ledger
- supplier ERP
- purchase orders
- warehouse management
- own courier network
- merchant payment collection for seller's customers
- Messenger bot
- AI assistant
- dark-mode project
- gamification
- iOS before product-market fit
- central courier scraping infrastructure

---

# 58. Defensibility data graph

```text
Customer
   ↓
Order
   ↓
Order Item / Product
   ↓
Consignment
   ↓
Courier Events
   ↓
Delivery Outcome
   ↓
COD Receivable
   ↓
Payout / Settlement
   ↓
Charge Components
   ↓
Profit Snapshot
```

Optional later:

```text
Ad Campaign → Orders → Delivered Orders → Contribution Profit
```

The goal is not lock-in by withholding data.

The goal is increasing value because accumulated history gives better:

- courier choices
- area decisions
- product decisions
- return diagnosis
- cashflow visibility
- settlement control

---

# 59. Key product metrics

Activation:

- shop created
- first order
- first courier linked
- first booking
- first payout matched

Weekly value metrics:

- active sellers with COD outstanding
- sellers who open Money screen
- reconciliation completed
- money mismatch caught
- Friday summary opened
- profit dashboard viewed

Retention:

- WAU/MAU
- sellers active ≥4 days/week
- week 4 / week 8 retention
- paid renewal
- plan downgrade reason

Business value:

- total COD reconciled
- amount of mismatches surfaced
- overdue COD identified
- return loss surfaced
- seller-reported money recovered/saved

---

# 60. Kill / pivot signals

Revisit the thesis if:

1. sellers don't maintain orders in the app
2. courier payout data cannot be accessed/imported reliably
3. reconciliation is not perceived as worth paying for
4. sellers only use the free risk checker
5. manual spreadsheet workflow remains faster
6. provider APIs change too frequently for solo maintenance
7. paid retention remains weak after sellers have accumulated history

---

# 61. Provider verification notes

These are external assumptions that must be re-verified before shipping each integration.

## Pathao

Verified from Pathao's official 2025 merchant API update:

- API order creation supports full recipient address with automatic area detection.
- Legacy city/zone/area fields can be omitted in that flow.
- Therefore structured-area fuzzy resolution is a fallback, not V1 core.

Source:
https://pathao.com/bn/blog/api-merchant-auto-address-feature/

## Google Play

Verified from current Google Play Payments policy/help used on 2026-09-09:

- Play-distributed apps selling digital app functionality/subscriptions generally must use Google Play Billing unless an allowed exception/program applies.
- In-app steering to alternative payment is restricted.
- Bangladesh is not in the cited current alternative billing country list.

Sources:
https://support.google.com/googleplay/android-developer/answer/9858738
https://support.google.com/googleplay/answer/11174377

## bKash

bKash's official business page currently lists:

- payment gateway
- tokenized checkout
- subscription payments
- APIs

Actual API credentials/features depend on merchant onboarding and contract.

Source:
https://www.bkash.com/en/business

## Steadfast

Community/SDK documentation widely describes:

- API key + secret auth
- single/bulk create
- status lookup
- balance
- return requests
- payments
- webhook support

Treat exact endpoints/payloads as **integration-time verification items** against the seller's current official merchant documentation/account, not immutable product assumptions.

---

# 62. Coding-agent rules

Claude Code/Codex must follow these rules:

1. This document is the source of truth.
2. Do not invent provider endpoints.
3. Keep provider logic inside adapters.
4. Never use float for money.
5. Never bypass tenant scope.
6. Never return encrypted credentials.
7. Never auto-retry ambiguous courier create calls.
8. Never change historical financial snapshots silently.
9. Never mix delivery status with COD settlement status.
10. Every side effect uses durable outbox/job semantics.
11. Every schema change uses Alembic migration.
12. Every financial rule change adds/updates golden tests.
13. Every provider status keeps raw original status.
14. Every payment activation requires server verification.
15. Every destructive admin action is audited.
16. Graceful degradation is preferred over total failure.
17. Offline mode may queue data mutations, but must not pretend external courier actions succeeded.
18. No new V1 feature unless it directly improves activation, daily money visibility, reconciliation, profit visibility, or reliability.

---

# 63. Final product definition

**CODPilot = Bangladesh Seller Financial Operations System**

Launch wedge:

> **Courier-এর কাছে কত টাকা আছে, কোন COD আটকে আছে, আর আসল profit কত—এক জায়গায়।**

Daily loop:

```text
Order
→ Book
→ Track
→ Deliver/Return
→ COD Receivable
→ Payout
→ Reconcile
→ Profit
→ Action
→ Next Order
```

If this loop becomes the seller's daily habit, CODPilot can expand into a broader Seller OS without becoming a bloated ERP.
---

# PART 7 — CLAUDE CODE EXECUTION CONTRACT

The sections below remove the remaining implementation ambiguity from the product specification above. They **do not remove or reduce any feature defined earlier**. If an earlier section is broad and a later section is more specific, the later section controls the implementation detail while the earlier feature remains in scope.

# 64. Source-of-truth and precedence rules

Claude Code/Codex must use this order of authority:

1. Financial and security invariants in this document
2. Explicit V1 scope and acceptance criteria
3. Domain/state-machine definitions
4. API/data contracts
5. Provider capability manifests verified against current official/current merchant documentation
6. UI/UX descriptions
7. Implementation convenience

Rules:

- Never delete a requested feature because it is difficult.
- Never invent provider behavior to make a feature appear complete.
- If a provider capability is unavailable, implement the defined fallback path and mark the capability unavailable.
- A stub counts as incomplete unless this file explicitly permits a stub.
- Do not silently substitute a different product behavior.
- Do not change the domain model to fit a provider; normalize the provider into the domain model.
- No direct production database edits outside migrations/admin repair tooling.
- No financial fix may be implemented only in UI. Financial truth lives server-side.
- No mobile-only entitlement enforcement for paid/server operations.

---

# 65. Final V1 outcome

At the end of V1 a real Bangladeshi F-commerce seller must be able to:

1. Sign in with phone OTP.
2. Create a shop.
3. Use manual mode without a courier API.
4. Connect and validate a supported courier account.
5. Add products with cost and stock.
6. Add/import customers/orders.
7. Paste a Messenger-style order and confirm parsed data.
8. See duplicate-order and customer-risk signals.
9. Book a real supported courier parcel.
10. Survive a booking timeout without accidentally duplicating the parcel.
11. Track delivery state from webhook/polling.
12. Handle return and partial-delivery cases.
13. See COD receivable created from the actual collectible outcome.
14. Import/enter a courier payout.
15. Reconcile exact matches automatically and ambiguous matches safely.
16. See unmatched, underpaid, overdue and disputed money.
17. See COD aging and courier-wise outstanding.
18. See per-order and product contribution profit.
19. See return loss.
20. Receive actionable alerts and the Friday summary.
21. Use the core order entry screens offline.
22. Export their own data.
23. Subscribe through the policy-compliant billing channel for their distribution source.
24. Retain read access to their historical business data after downgrade.

If any item above is missing, V1 is not complete.

---

# 66. Monorepo layout

Use one repository unless there is an exceptional reason.

```text
codpilot/
  apps/
    mobile_flutter/
    web_next/                  # V2; may be absent in initial V1 branch
    admin_web/                 # minimal internal ops console
  backend/
    app/
    migrations/
    tests/
    scripts/
  packages/
    api_contracts/             # OpenAPI-generated/client artifacts if used
    domain_fixtures/           # golden financial fixtures
  infra/
    docker/
    compose/
    nginx_or_caddy/
    monitoring/
  docs/
    CODPILOT_MASTER_SPEC.md
    ADR/
    provider_notes/
    runbooks/
  .github/
    workflows/
  Makefile
  .env.example
  docker-compose.yml
  README.md
```

No courier-specific business logic inside generic order/money modules.

---

# 67. Architecture Decision Records (ADR)

Before changing a major frozen decision, create an ADR in `docs/ADR/`.

ADR required for changes to:

- Flutter/Riverpod/Drift
- FastAPI/Postgres
- modular monolith boundary
- money storage
- financial ledger
- tenancy model
- provider adapter contract
- offline sync strategy
- billing channel strategy
- source-of-truth rules
- deletion/retention policy

ADR format:

```text
Context
Decision
Alternatives considered
Consequences
Migration/rollback
Approval status
```

An AI agent may propose an ADR but must not silently override this spec.

---

# 68. Environment contract

Required environments:

```text
local
test
staging
production
```

Never share credentials across staging and production.

Required configuration groups:

```text
APP_ENV
PUBLIC_BASE_URL

DATABASE_URL
REDIS_URL

JWT_SIGNING_KEY
JWT_KEY_VERSION
CREDENTIAL_ENCRYPTION_KEY
CREDENTIAL_KEY_VERSION

OTP_PROVIDER
OTP_PROVIDER_SECRET

FCM credentials
SMS provider credentials

SENTRY_DSN

R2 endpoint/bucket/access keys

PLAY package name / service-account verification config
BKASH web credentials (only when merchant onboarding provides them)

provider feature flags
```

`.env.example` contains variable names only, never real secrets.

Startup must fail fast if critical production secrets are missing.

---

# 69. Time, timezone and business-day semantics

Store all timestamps in UTC.

Display and business summaries default to:

```text
Asia/Dhaka
```

Store tenant timezone even if V1 defaults every tenant to Asia/Dhaka.

Fields representing a seller's business date use an explicit `DATE`, not inferred UTC date.

Friday summary means Friday in tenant timezone.

Never calculate subscription expiration or COD aging by local device clock.

---

# 70. IDs and order numbering

Database primary keys:

- UUIDv7 where supported, otherwise UUID
- never expose incremental DB IDs as authorization assumptions

Seller-facing order number:

```text
CP-20260909-0042
```

or tenant-configured compact equivalent.

Requirements:

- unique within tenant
- human searchable
- provider merchant reference derived from stable order/booking attempt identifiers
- never reuse a cancelled order number

---

# 71. Address model

Keep both:

```text
raw_address
normalized_address
```

Optional structured analytics fields:

```text
division
district
city
area
postal_code
lat/lng later
provider_location_refs JSONB
```

Rules:

- raw seller/customer text is retained as evidence.
- provider-transformed address is stored as a separate snapshot.
- never overwrite the original address with a courier-normalized string.
- Pathao/provider auto-address is an integration concern, not the canonical customer address.
- seller confirmation is required if the provider returns ambiguity that materially changes delivery location.

---

# 72. Missing entity required for partial delivery: fulfillment lines

Add:

```text
consignment_items
- id
- tenant_id
- consignment_id
- order_item_id
- qty_shipped
- qty_delivered
- qty_returned
- unit_collectible_paisa
- unit_cost_snapshot_paisa
```

Why:

An order may contain several items and may produce multiple consignments. A partial delivery cannot be modeled correctly with only an order-level COD amount.

Rules:

- `qty_delivered + qty_returned <= qty_shipped`
- realized revenue for a partial delivery is derived from delivered units/verified collectible amount
- stock restore works from returned units
- profit uses cost snapshot of the fulfilled items

---

# 73. Courier pickup/store model

Add:

```text
courier_stores
- id
- tenant_id
- courier_account_id
- provider_store_id
- name
- pickup_address
- raw JSONB
- active
```

A courier account may contain multiple stores/pickup locations.

Never put one `store_id` field on the account as the only representation.

---

# 74. Provider integration manifest

Every provider gets a versioned manifest:

```yaml
provider: steadfast
verified_at: YYYY-MM-DD
documentation_source: ...
capabilities:
  credential_validation: true
  create_single: true
  create_bulk: unknown|true|false
  status_lookup: true
  webhook: true|false|unknown
  payouts: true|false|unknown
  balance: true|false|unknown
  returns: true|false|unknown
  customer_stats: true|false|unknown
notes:
  - ...
```

Rules:

- `unknown` is valid.
- Never convert unknown to true because a community SDK implements it.
- Before coding an endpoint, verify it against current official/merchant documentation or a live test account.
- Provider payload samples are stored redacted under `docs/provider_notes/`.

---

# 75. Provider capability degradation

The UI must react to capability, not provider name.

Examples:

If `payouts=false`:

> "এই courier-এর payout API পাওয়া যায়নি — statement upload করুন।"

If `price_quote=false`:

> "Live charge unavailable — saved/estimated rate shown."

If `customer_stats=false`:

> mark provider unavailable inside the risk card; do not fail the entire risk result.

If provider health is degraded:

- allow offline/manual order work
- block only the affected network action
- keep historical data available

---

# 76. Concurrency safety

Use DB locking/atomic transitions on money and booking paths.

Required examples:

### Booking

Two devices pressing Book simultaneously must create one controlled booking attempt.

Use:

- row lock on order/active booking attempt, or
- atomic unique constraint + transaction

### Payout reconciliation

Two workers must not apply the same payout line twice.

### Stock

Concurrent bookings cannot decrement stock below allowed policy without explicit oversell setting.

### Subscription

Multiple payment notifications cannot create duplicate entitlement periods.

---

# 77. Idempotency contract

Client-mutating endpoints accept:

```text
Idempotency-Key
```

for operations where a network retry could duplicate money/external work.

At minimum:

- booking
- manual payout creation
- payout import finalization
- billing checkout creation
- sensitive bulk actions

Store:

```text
tenant_id
idempotency_key
endpoint
request_hash
response_code
response_body/reference
expires_at
```

Same key + different request hash returns conflict.

---

# 78. Webhook deduplication — robust rule

Do not rely only on `(status, timestamp)`.

Preferred dedupe priority:

1. provider event ID, if present
2. provider transaction/event reference
3. HMAC/hash fingerprint of stable normalized payload + provider + entity reference

Store raw body hash.

Webhook event table adds:

```text
provider_event_id nullable
payload_sha256
processing_status
processed_at
failure_reason
```

A replayed webhook may return success but must not repeat the domain side effect.

---

# 79. Webhook security

Where provider supports signatures:

- verify signature against raw request body
- constant-time comparison
- timestamp/replay window where available

Where provider provides only secret URL/token:

- use high-entropy tenant/provider token
- rotate token
- request size limit
- rate limit
- optional IP allowlist only as additional control, never sole trust if IPs can change

Persist rejected webhook metadata without sensitive body when useful for incident debugging.

---

# 80. Immutable seller financial ledger

Add an append-only operational ledger. This is **not** a full accounting/general-ledger product.

Table:

```text
financial_ledger_entries
- id
- tenant_id
- occurred_at
- business_date
- entity_type
- entity_id
- event_type
- currency
- amount_paisa
- direction            # debit/credit from seller-money perspective
- bucket
- source
- source_ref
- reversal_of nullable
- metadata JSONB
- created_at
```

Buckets:

```text
COD_RECEIVABLE
COD_SETTLED
COURIER_CHARGE
RETURN_CHARGE
COD_FEE
ADJUSTMENT
WRITE_OFF
REFUND
OTHER
```

Examples:

```text
DELIVERY_CONFIRMED       +৳1,405 → COD_RECEIVABLE
PAYOUT_APPLIED           -৳1,405 → COD_RECEIVABLE
PAYOUT_APPLIED           +৳1,405 → COD_SETTLED
RETURN_FEE               -৳80    → charge/profit impact
MANUAL_ADJUSTMENT        ±amount
```

Rules:

- ledger entry is never edited.
- correction is a reversal + new entry.
- operational states may be rebuilt/checked against ledger totals.
- user-facing balances are derived/materialized from ledger + domain tables.
- every manual financial correction requires actor, reason and audit event.

---

# 81. Financial reconciliation invariants

In addition to earlier invariants:

1. Every payout has a unique provider/reference identity where available.
2. Re-importing the same statement must be idempotent.
3. Payout line can be `UNMATCHED` without blocking other lines.
4. Reconciliation never deletes source statement data.
5. Auto-match confidence threshold is configurable and tested.
6. Ambiguous equal-amount candidates do not auto-match.
7. A manual match records who matched it and why.
8. Unmatch/re-match is implemented as reversal, not destructive overwrite.
9. Provider adjustments/deductions can be represented separately from parcel principal.
10. Financial dashboard totals reconcile to ledger/source totals within defined rounding rules.

---

# 82. Reconciliation candidate scoring — replace unsafe greedy-only logic

Oldest-first may be used only as a suggestion when no stronger reference exists.

Candidate score inputs:

```text
exact provider consignment ID      +100
exact tracking code                +100
exact merchant invoice/reference   +100
same provider                      mandatory
exact expected net amount          +35
within amount tolerance            +20
delivery date in expected window   +15
same normalized phone              +10
same order number fragment         +20
already settled                    reject
terminal return/cancel conflict    reject/penalize
```

Rules:

- exact unique reference → auto-match
- high-score unique candidate → may auto-match only above configured threshold
- tie/near-tie → manual review
- amount-only match → never auto-match
- manual payout with only lump total may generate proposed allocation, not irreversible settlement

Show seller:

```text
38 exact matches
2 suggested matches
1 unresolved
```

---

# 83. Payout source preservation

Add:

```text
payout_source_files
- id
- tenant_id
- provider
- storage_key
- sha256
- original_filename
- uploaded_by
- imported_at
```

Store parsed row raw JSON on `payout_lines` or `import_rows`.

This allows support to explain a reconciliation result later.

---

# 84. Payout adjustments

Add:

```text
payout_adjustments
- id
- payout_id
- consignment_id nullable
- type
- amount_paisa
- provider_label
- raw_text
- recognized_rule
```

Types:

- COD fee
- delivery fee
- return fee
- tax/VAT
- bonus
- penalty
- manual adjustment
- unknown deduction

Unknown deductions must remain visible, not silently forced into delivery fee.

---

# 85. Profit truth hierarchy

For each cost/revenue field use:

1. settled/actual provider value
2. booked provider snapshot
3. seller-entered actual
4. configured estimate
5. unknown

UI may display:

```text
Actual
Estimated
Missing
```

Do not show an estimated profit as exact without a marker.

---

# 86. Expense allocation

Manual ad spend/fixed expense allocation must be explicit.

Supported V1 ad allocation methods:

- equal per delivered order
- proportional to realized revenue
- product-tagged spend / delivered units

Store:

```text
allocation_method
allocation_version
source_expense_id
```

Do not rewrite historical profit silently when seller changes future allocation preference.

---

# 87. Return settlement timing

Do not hard-code "return takes N days".

Use:

- provider event timestamps
- configured alert threshold
- seller/provider historical median later

Default stale thresholds are configurable per provider.

UI copy:

> "Expected window exceeded"

rather than falsely asserting provider owes money by a legally fixed date.

---

# 88. RBAC permission matrix

Roles:

```text
OWNER
MANAGER
PACKER
ACCOUNTANT
VIEWER
```

Example permissions:

| Permission | Owner | Manager | Packer | Accountant | Viewer |
|---|---|---|---|---|---|
| View orders | ✅ | ✅ | ✅ | ✅ | ✅ |
| Create/edit unbooked order | ✅ | ✅ | ✅ | ❌ | ❌ |
| Book courier | ✅ | ✅ | ✅ optional | ❌ | ❌ |
| Cancel/return request | ✅ | ✅ | ❌ | ❌ | ❌ |
| View customer risk | ✅ | ✅ | ✅ | ❌ | optional |
| View financial totals | ✅ | ✅ | ❌ | ✅ | configurable |
| Reconcile payout | ✅ | optional | ❌ | ✅ | ❌ |
| Manual financial correction | ✅ | ❌ | ❌ | restricted | ❌ |
| Export customer data | ✅ | configurable | ❌ | configurable | ❌ |
| Manage courier credentials | ✅ | ❌ | ❌ | ❌ | ❌ |
| Billing/team/settings | ✅ | ❌ | ❌ | ❌ | ❌ |

Permissions are server-side.

V1 may expose only Owner until team ships, but schema and authorization primitives must not require a rewrite.

---

# 89. Auth/session tables

Add:

```text
otp_challenges
auth_sessions
refresh_tokens
devices
```

`devices` tracks:

- app version
- platform
- push token
- last seen
- revoked

Session revocation should work without changing password because login is OTP-based.

---

# 90. Play Billing server verification

For Play-distributed subscriptions:

Flow:

```text
App launches Play purchase
→ Play returns purchase token
→ App sends token + product/package to backend
→ Backend verifies using Google Play Developer API/current supported verification mechanism
→ Validate package/product/account binding and purchase state
→ acknowledge purchase when required
→ write billing transaction
→ grant entitlement
```

Required:

- purchase token uniqueness
- replay protection
- renewal/cancellation state sync
- Real-time Developer Notifications / current equivalent when configured
- periodic reconciliation job for missed notifications
- restore purchases/login reconciliation
- never grant paid entitlement solely from a client callback

The exact current Google APIs/libraries must be verified at implementation time.

---

# 91. bKash web/direct billing contract

Official bKash business offerings currently include payment gateway, tokenized checkout and subscription payments, but merchant/API details depend on onboarding.

Therefore:

- implement a `BillingProvider` interface
- do not hard-code undocumented endpoint paths into the master domain
- store provider reference/agreement ID only after verified server response
- verify callbacks/webhooks according to the current merchant contract
- model failed recurring payment/dunning generically
- support one-time web payment if subscription mandate is not enabled for the merchant
- do not expose bKash purchase links in a Play build where Play policy forbids steering

---

# 92. Billing provider interface

```python
class BillingProvider(Protocol):
    async def create_checkout(self, tenant, plan, invoice) -> CheckoutSession: ...
    async def verify_purchase(self, payload) -> VerifiedPurchase: ...
    async def cancel_subscription(self, subscription) -> CancelResult: ...
    async def sync_subscription(self, subscription) -> SubscriptionState: ...
    async def refund(self, transaction, amount) -> RefundResult | None: ...
    def verify_webhook(self, headers, body: bytes) -> bool: ...
    def parse_webhook(self, body: bytes) -> list[BillingEvent]: ...
```

Implementations:

```text
GooglePlayBillingProvider
BkashWebBillingProvider
AdminManualProvider  # support/test-controlled only
```

---

# 93. Subscription model improvements

Add:

```text
billing_transactions
subscription_events
play_purchase_tokens
billing_provider_customers
```

Subscription state:

```text
TRIAL
ACTIVE
GRACE
PAST_DUE
CANCEL_AT_PERIOD_END
CANCELLED
EXPIRED
REFUNDED
SUSPENDED
```

Entitlement derives from verified subscription state, not vice versa.

---

# 94. Notification center

Push is not enough.

Store in-app notifications:

```text
notifications
- id
- tenant_id
- user_id nullable
- type
- severity
- title
- body
- entity_type
- entity_id
- read_at
- created_at
```

Severity:

```text
INFO
ACTION
WARNING
CRITICAL
```

Deduplicate repetitive warnings.

Opening a push must deep-link to the exact order/payout/reconciliation case.

---

# 95. Alert fatigue rules

Do not send one push per parcel event.

Push only if:

- seller action is required
- material money changed
- defined threshold is crossed
- seller explicitly opted into routine tracking push

Bundle low-priority items.

Example:

> "5 parcels need attention"

instead of five separate pushes.

---

# 96. AI paste parser contract

Normalized response:

```json
{
  "customer_name": null,
  "phones": [],
  "selected_phone": null,
  "address": null,
  "items": [
    {
      "name": "",
      "quantity": 1,
      "size": null,
      "color": null,
      "unit_price_paisa": null
    }
  ],
  "cod_amount_paisa": null,
  "notes": null,
  "confidence": {
    "phone": 0.0,
    "address": 0.0,
    "items": 0.0,
    "amount": 0.0
  }
}
```

Rules:

- deterministic parser first
- LLM only below confidence threshold or user chooses enhanced parse
- strict schema validation
- max input length
- prompt injection text is treated as data, never instruction
- never include credentials/system data in prompt
- configurable model/provider
- cost per parse recorded
- parsing failure returns editable original text, never loses order data

---

# 97. AI cost guardrail

Track:

```text
ai_usage
- tenant_id
- feature
- model
- input_units
- output_units
- estimated_cost
- created_at
```

Monthly hard quota by entitlement.

If AI is unavailable:

> deterministic/manual order entry still works.

AI outage must not block business operations.

---

# 98. Import framework

Generic importer:

```text
UPLOAD
→ detect CSV/XLSX
→ choose preset/provider
→ column mapping
→ preview
→ validate
→ dry run
→ commit
→ report
```

Every import has:

- import ID
- source SHA256
- row count
- success count
- warning count
- failure count
- idempotent duplicate handling

Templates:

- Orders
- Products
- Courier payout/statement

Do not silently coerce invalid money/phone values.

---

# 99. Export framework

Exports:

- orders
- customers
- products
- payouts/reconciliation
- profit summary

Generate asynchronously for large datasets.

Export links:

- time-limited
- authenticated
- audited

Never email raw customer exports automatically.

---

# 100. Data deletion and privacy workflow

User-facing:

- delete individual draft/unbooked records where safe
- archive/soft-delete business records
- request account deletion

Financial/courier records may require retention for legitimate operational/audit reasons. Document the retention policy clearly.

Account deletion workflow:

1. ownership confirmation
2. cancel active subscription
3. revoke sessions/credentials
4. disable integrations
5. schedule deletion/anonymization according to policy
6. preserve only records legitimately required, minimized
7. final audit event

No public cross-seller blacklist.

---

# 101. Data masking

UI/admin log masking:

```text
01712****78
```

Reveal full phone only to authorized seller role where operationally needed.

Admin/support sees masked data by default.

"Reveal" actions, if implemented, are audited.

---

# 102. Support case model

Add:

```text
support_cases
- id
- tenant_id
- type
- severity
- order_id nullable
- consignment_id nullable
- payout_id nullable
- reconciliation_case_id nullable
- status
- assigned_to
- created_at
- resolved_at
```

Support bundle may include:

- relevant IDs
- redacted provider trace
- status timeline
- payout source line
- ledger entries
- app version

Never require seller to send courier passwords over chat.

---

# 103. Internal repair tools

Allowed repair actions:

- reprocess webhook
- rerun payout parser
- reverse manual match
- controlled rematch
- force provider status refresh
- rebuild materialized financial summary from ledger
- revoke courier credential
- extend plan as support credit
- disable broken capability

Each action:

- permission checked
- requires reason
- audited
- idempotent where possible

No hidden "edit database row" button.

---

# 104. Materialized summaries

For fast dashboard queries, materialize/cache:

```text
tenant_daily_metrics
tenant_money_summary
product_period_metrics
courier_period_metrics
area_period_metrics
```

Source of truth remains transactional data + ledger.

Provide rebuild jobs.

Never make a cache the only copy of a financial fact.

---

# 105. Performance targets

Initial targets under normal load:

- cached Home summary: p95 < 500 ms backend
- order list first page: p95 < 500 ms
- normal CRUD: p95 < 400 ms
- risk lookup: provider dependent; UI shows per-provider progress/partial result
- courier booking: provider dependent; no infinite spinner
- webhook ingress: persist + respond quickly
- cold mobile launch: monitor on low-end device
- local order creation: immediate/offline-capable

These are engineering targets, not SLA promises to customers.

---

# 106. Pagination and query limits

Never return unlimited orders/customers.

Use cursor pagination for high-volume mutable lists.

Default/max page sizes configurable, e.g.:

```text
default 30
max 100
```

All search/filter queries must have indexes tested with realistic data.

---

# 107. Database indexes

At minimum consider:

```text
orders(tenant_id, created_at)
orders(tenant_id, status, created_at)
customers(tenant_id, normalized_phone_hash/search_key)
consignments(tenant_id, provider, status)
consignments(courier_account_id, merchant_ref)
consignments(provider, consignment_id)
cod_receivables(tenant_id, state, due_at)
payouts(tenant_id, provider, paid_at)
reconciliation_cases(tenant_id, status)
courier_events(consignment_id, received_at)
financial_ledger_entries(tenant_id, occurred_at)
notifications(tenant_id, read_at, created_at)
```

Use `EXPLAIN ANALYZE` on core dashboard/reconciliation queries before production.

---

# 108. Database constraints

Use DB constraints for invariants that must survive application bugs:

- unique provider consignment IDs where provider scope permits
- unique merchant reference per courier account/provider
- positive quantity
- non-negative required monetary components where domain requires
- check delivered/returned quantities
- FK ownership consistency
- unique active tenant role/user rows
- unique provider payout reference where available

Application validation is not enough.

---

# 109. Migration rules

Every schema change:

1. Alembic migration
2. backward-compatible deploy where possible
3. data backfill separated from long blocking transaction
4. rollback or forward-fix plan documented
5. test against a production-like copy/synthetic scale
6. never drop a column used by the previous app/server version in the same deployment step

Use expand → migrate → contract for risky changes.

---

# 110. API compatibility

API version prefix:

```text
/v1/
```

Mobile app may lag backend.

Therefore:

- additive changes preferred
- do not rename/remove required fields without version/migration strategy
- server can reject unsupported app versions only through explicit minimum-version configuration
- include API/app version in diagnostic headers/logs

---

# 111. Feature rollout

Provider and risky features use:

```text
internal
beta tenants
5%
25%
100%
```

Roll back with feature flag.

Examples:

- new payout matcher
- new Pathao adapter version
- AI parser provider
- courier recommendation

Financial algorithm rollouts must compare old/new results in shadow mode before switching where feasible.

---

# 112. Shadow reconciliation mode

Before enabling a new auto-match rule:

1. run it without changing settlement state
2. compare with human/existing exact matches
3. measure false-match risk
4. require high precision
5. then enable auto-match threshold gradually

A reconciliation product optimizes **precision before recall**.

Missing an auto-match is inconvenience. Wrongly settling money is loss of trust.

---

# 113. Provider sandbox/mock strategy

For each adapter create recorded/redacted fixtures:

```text
credential valid
credential invalid
create success
create validation error
create timeout-after-success
status sequence
return sequence
duplicate webhook
payout with exact refs
payout with deductions
provider 500
provider malformed JSON
```

Tests never depend entirely on live provider uptime.

Live smoke test exists separately and is not run on every CI build.

---

# 114. CI pipeline

Every PR:

```text
backend lint/typecheck
backend unit tests
financial golden tests
adapter contract tests
migration sanity
Flutter analyze
Flutter unit/widget tests
security secret scan
build debug/test artifacts
```

Main/staging additionally:

- integration tests
- Docker image build
- migration dry run
- staging deploy
- smoke tests

Production deployment requires green staging and explicit release action.

---

# 115. Release and rollback

Backend:

- immutable Docker image tag
- migration step
- health checks
- rollback application image
- database rollback only when safe; otherwise forward fix

Mobile:

- internal testing
- closed beta
- staged Play rollout
- crash-free monitoring
- server feature flags protect provider/billing features from old clients

Never make a critical backend change that instantly breaks the previous mobile version.

---

# 116. Health endpoints

Expose authenticated/internal or safe:

```text
/health/live
/health/ready
```

Readiness verifies required dependencies:

- DB
- critical migration level
- Redis when required

Do not make readiness call every external courier.

Provider health is separate.

---

# 117. SLO-style operational indicators

Track:

- booking unknown rate
- duplicate booking incident count
- payout auto-match precision sample
- unmatched payout amount
- webhook processing delay
- provider sync backlog
- sync conflict rate
- crash-free mobile sessions
- OTP delivery success
- subscription verification failures

P0 incident examples:

- duplicate courier bookings
- incorrect settlement applied to wrong parcel
- cross-tenant data exposure
- paid entitlement granted without verification
- corrupted historical profit
- leaked courier credential

---

# 118. Incident runbooks

Create runbooks for:

```text
PROVIDER_OUTAGE.md
BOOKING_UNKNOWN_SPIKE.md
DUPLICATE_BOOKING.md
RECONCILIATION_ERROR.md
WEBHOOK_BACKLOG.md
BILLING_VERIFICATION_FAILURE.md
CREDENTIAL_LEAK.md
DATABASE_RESTORE.md
```

Each runbook:

- detection
- immediate containment
- feature flag
- data query
- seller impact
- recovery
- post-incident verification

---

# 119. Analytics/event tracking

Product events:

```text
signup_started
otp_verified
shop_created
courier_link_started
courier_linked
first_order_created
paste_parse_used
risk_check_completed
booking_started
booking_succeeded
booking_unknown
payout_imported
reconciliation_completed
money_screen_viewed
profit_viewed
friday_summary_opened
subscription_started
subscription_renewed
subscription_failed
export_created
```

Do not send raw customer PII to product analytics.

Activation funnel:

```text
OTP
→ Shop
→ Order
→ Courier linked/manual
→ Booking
→ First delivered
→ First payout/reconciliation
```

---

# 120. Seller benchmark policy

The category field may support future benchmarks, but V1 must **not** show fabricated benchmarks like:

> "কাপড়ের গড় return rate 9%"

unless a sufficient, privacy-safe dataset exists.

Future benchmark must:

- have minimum sample size
- be aggregated/anonymized
- disclose period and segment
- suppress small cohorts
- never expose another seller

Until then show:

> "Your last 30 days vs previous 30 days."

---

# 121. Courier recommendation safety

Recommendation requires:

- enough completed shipments
- sample threshold per relevant segment
- confidence
- current provider availability
- cost snapshot freshness

Never recommend using invented industry-wide success rates.

Fallback:

> "Not enough history to recommend yet."

---

# 122. UI design system

Use a compact seller-operations design language.

Core semantic components:

```text
MoneyCard
MetricTile
AttentionCard
StatusChip
ProviderBadge
RiskBadge
AmountDelta
Timeline
BottomActionBar
OfflineBanner
ProviderHealthBanner
EmptyState
SkeletonList
```

Semantic colors may be implemented in design tokens; business meaning:

- success
- warning
- danger
- info
- neutral

Do not communicate state by color alone; always include icon/text.

---

# 123. Money display rules

Examples:

```text
৳1,250
-৳80
+৳1,405
```

Use Bangladeshi grouping consistently according to product design decision.

Exact paisa can be hidden when zero but retained internally.

Negative amount must be visually explicit.

Estimated values:

```text
~৳1,405 estimated
```

Actual:

```text
৳1,405 settled
```

---

# 124. Accessibility and usability

- screen-reader labels on primary actions
- minimum touch target
- scalable text
- numeric fields announce units
- status has text, not icon only
- error message is actionable
- confirmation on high-risk financial/manual correction actions
- undo where safe
- no confirmation spam for normal order entry

---

# 125. Android permissions minimization

V1 should request only permissions truly required by implemented features.

Examples:

- notifications: runtime permission where Android version requires it
- camera/photo only if product image capture/upload is shipped
- no SMS inbox/read permission for OTP; prefer SMS Retriever/one-time-code mechanisms that do not require broad inbox access
- no contacts permission
- no call-log permission
- no background location

Every permission must have a documented feature reason.

---

# 126. Network and retry UX

For read actions:

- retry safely
- cached last-known data may be shown with timestamp

For external write actions:

- show explicit state
- do not let user trigger uncontrolled duplicate retry

Examples:

```text
Booking…
Checking previous attempt…
Booked ✓
Could not confirm — we're verifying this attempt
```

Never show "Failed" after an ambiguous provider timeout until reconciliation establishes failure.

---

# 127. Offline mutation states

Local records:

```text
LOCAL_ONLY
SYNCING
SYNCED
CONFLICT
FAILED_VALIDATION
```

A seller can see which orders are not yet on server.

Critical provider actions are never represented as locally successful.

---

# 128. Sync conflict UI

For rare editable-field conflict:

```text
Your version
Server version
```

Allow seller to choose where safe.

Courier/payment fields are never client-choice conflicts.

---

# 129. Search normalization

Order/customer search supports:

- normalized phone
- last digits
- order number
- tracking code
- customer name
- product/SKU
- provider reference

Bangla/English numeral normalization happens before search.

---

# 130. Fraud/risk legal/product wording

Approved concepts:

```text
Order risk
Delivery history
Previous delivery pattern
Low / Medium / High / Unknown
```

Avoid:

```text
Fraud person
Scammer
Criminal
Blacklist shared across sellers
```

Seller-private block notes are allowed and remain private.

---

# 131. SMS encoding/cost implementation

Do not hard-code a single segment size rule without actually running the selected gateway's encoding calculation.

Before send:

- normalize template
- calculate provider-reported/local segment count
- estimate cost
- enforce quota by segments, not message rows where appropriate

Store actual charged segments if provider returns them.

Banglish is preferred for concise transactional SMS when it materially reduces segment cost and remains understandable.

---

# 132. External API secret rotation

Courier credential record:

```text
credentials_enc
key_version
last_validated_at
last_used_at
failure_count
```

On repeated auth failure:

- mark `NEEDS_RECONNECT`
- stop hammering provider
- notify owner
- keep historical data

Encryption key rotation supports decrypt-old/encrypt-new migration.

---

# 133. Customer phone exact-search protection

Because deterministic encrypted text is risky for searchable PII, store:

- encrypted full phone for display
- keyed HMAC/search hash of canonical E.164 for exact lookup

Example:

```text
phone_enc
phone_search_hmac
phone_last4
```

Use a separate secret for the HMAC.

Do not use plain unsalted SHA256 of phone numbers.

---

# 134. Financial correction workflow

Manual correction screen requires:

- affected object
- old balance
- correction amount
- reason code
- note
- preview of resulting balance
- confirmation

Creates:

- audit event
- financial ledger adjustment/reversal
- recomputed summary

Never directly update settled totals.

---

# 135. Data quality badges

Useful screens may show:

```text
Complete
Estimated
Missing cost
Unreconciled
```

Example profit card:

> Profit ৳333 — **Estimated** (ad cost missing)

This is better than false precision.

---

# 136. V1 product scope — frozen checklist

Do not drop any checked item:

### Account
- [x] Phone OTP
- [x] Shop setup
- [x] Manual mode
- [x] Courier credential linking/validation

### Orders
- [x] Manual order
- [x] Paste parse
- [x] CSV import
- [x] Customer autofill
- [x] Duplicate warning
- [x] Filters/search
- [x] Order timeline

### Risk
- [x] Standalone risk lookup
- [x] Auto risk on paid order flow
- [x] Per-provider partial results
- [x] Cache/quota
- [x] safe wording

### Products/inventory
- [x] Product + cost + selling price
- [x] Stock
- [x] Stock movement ledger
- [x] Return restore/write-off
- [x] Consignment item mapping for partial delivery

### Courier
- [x] Adapter framework
- [x] Steadfast real integration first
- [x] Manual courier fallback
- [x] Booking
- [x] Bulk-ready architecture
- [x] Status sync
- [x] Webhook + polling
- [x] Booking unknown reconciliation
- [x] Provider health

### Money
- [x] COD receivables
- [x] Money screen
- [x] COD aging
- [x] Payout API where verified
- [x] Statement upload
- [x] Manual payout
- [x] Exact/safe matching
- [x] mismatch/dispute
- [x] immutable operational money ledger
- [x] unknown deductions
- [x] alerts

### Profit
- [x] Per-order P&L
- [x] Product profitability
- [x] Return loss
- [x] expense entry
- [x] estimated vs actual indicators
- [x] immutable/versioned snapshot

### Customers
- [x] CRM auto-built
- [x] seller-private notes/star/block
- [x] repeat buyer
- [x] own-business success history

### Communication
- [x] Tracking SMS
- [x] FCM
- [x] In-app notification center
- [x] Friday summary

### Platform
- [x] Drift offline data
- [x] mutation sync
- [x] server-authoritative courier/money state
- [x] export
- [x] feature flags
- [x] internal admin/ops
- [x] audit logs
- [x] Sentry/health/metrics
- [x] backup restore verification

### Billing
- [x] unified entitlement service
- [x] Play Billing path for Play-distributed digital subscription
- [x] bKash web/direct path where merchant contract permits
- [x] server verification
- [x] downgrade without data deletion

---

# 137. V1.1 / V2 preserved features

These features from the product vision are **not removed**; they are scheduled after V1 reliability:

- Pathao
- RedX
- more couriers
- multi-user team UI
- web seller dashboard
- product variants
- bulk risk
- richer CSV mappings
- courier scorecard
- area performance
- cashflow forecast
- ad allocation/ROAS
- advanced return analytics
- customer campaigns
- confirmation/OTP SMS
- courier recommendation

Later:

- Messenger/Page integration
- WhatsApp where policy/provider permits
- loyalty/repeat automation
- advanced AI insights

---

# 138. Claude Code phase protocol

Do **not** ask Claude Code to build the entire product in one uncontrolled pass.

For each phase Claude Code must:

1. read this whole file
2. state the exact phase scope
3. inspect current repository state
4. produce/update implementation plan
5. identify migrations
6. implement smallest complete vertical slices
7. write tests before/with critical financial logic
8. run all relevant tests
9. fix failures
10. update docs/provider manifest
11. produce a concise completion report:
   - files changed
   - migrations
   - tests run/results
   - manual steps
   - unverified external assumptions
   - remaining phase items
12. commit only after the phase is green if the operator requests commits

Never claim an external integration works without a live/sandbox verification when the provider requires one.

---

# 139. Claude Code recommended phase prompts

## Phase A — foundation

```text
Read CODPILOT_MASTER_SPEC.md completely. Implement only Foundation:
FastAPI modular skeleton, Postgres/SQLAlchemy/Alembic, tenancy, OTP auth contracts,
Flutter shell, Riverpod, Drift, local outbox, sync contracts, Docker local stack,
Sentry hooks, health endpoints and CI. Do not implement courier business behavior yet.
All tenant isolation tests must pass.
```

## Phase B — orders/products/customers

```text
Implement the commerce core from the master spec:
products, stock movements, customers, orders, order_items, consignment_items,
manual entry, paste parser layer 1, import framework, duplicate detection, offline sync.
No fake courier data in production paths.
```

## Phase C — Steadfast adapter

```text
Verify current Steadfast merchant API capabilities with the supplied official/merchant
documentation and credentials. Update provider manifest first. Implement only verified
capabilities through CourierAdapter, including credential validation, real booking,
status, webhook/polling and BOOKING_UNKNOWN recovery. Never invent endpoints.
```

## Phase D — money/reconciliation

```text
Implement COD receivables, financial ledger, payouts, payout source preservation,
reconciliation scoring, mismatch/dispute cases, COD aging and Money screen APIs.
Run every golden financial scenario. Precision is more important than auto-match rate.
```

## Phase E — profit/returns/alerts

```text
Implement charge snapshots, P&L snapshots, return economics, expenses,
data-quality indicators, alerts, in-app notification center and Friday summary.
Historical settled profit must remain immutable except through versioned corrections.
```

## Phase F — billing/admin/hardening

```text
Implement entitlements, current-policy Play Billing server verification path,
bKash web provider abstraction, internal admin repair tooling, RBAC primitives,
audit, export, backup verification, provider health, feature flags and release gates.
```

---

# 140. Anti-hallucination rules for coding agents

Claude/Codex must write `UNVERIFIED` instead of guessing.

Never invent:

- courier endpoint URL
- auth header
- webhook signature
- rate limit
- payout field
- status mapping
- bKash endpoint
- Google billing behavior
- SMS gateway cost
- courier charge
- delivery SLA
- market benchmark

If documentation conflicts:

1. prefer current official/merchant docs
2. test against non-destructive live/sandbox endpoint
3. record the result in provider manifest
4. feature-flag uncertain behavior

---

# 141. Definition of Done — per feature

A feature is done only when:

- domain rule implemented server-side
- migrations included
- API contract documented
- mobile UX implemented where in V1
- loading/empty/error/offline states handled
- authorization tested
- tenant isolation tested
- analytics/operational logging added where meaningful
- unit/integration tests pass
- no secret/PII logging
- acceptance scenario manually tested
- external provider assumption clearly marked verified/unverified
- no TODO that blocks normal production use

A beautiful screen backed by dummy data is not done.

---

# 142. Definition of Done — release

V1 release candidate requires:

### Financial safety
- no known duplicate booking bug
- golden financial tests green
- reconciliation re-import idempotent
- manual corrections audited
- partial delivery tested
- historical rate change tested

### Security
- tenant isolation test suite green
- secrets scan green
- OTP abuse limits
- credential encryption
- export audit
- webhook verification/fallback protections

### Reliability
- staging restore tested
- provider outage behavior tested
- booking unknown recovery tested
- previous app version remains compatible during rollout
- crash/error monitoring active

### Product
- real seller can complete activation loop
- Money screen populated from real data
- at least one real courier integration verified
- statement/manual payout works even if payout API unavailable
- downgrade preserves history

---

# 143. Suggested validation dataset before launch

Collect redacted real-world examples:

- 100+ orders across sellers
- delivered
- return
- cancelled
- partial delivery if available
- duplicate phone/order cases
- multiple payout statements
- split payout
- underpayment/deduction
- courier charge variations
- Bangla/Banglish addresses
- Bangla numeral phone
- messy Messenger order text

Use them as test fixtures only after anonymization/redaction.

---

# 144. Final architecture sanity check

The final system must remain understandable as:

```text
Seller action
   ↓
Order
   ↓
Fulfillment / Consignment
   ↓
Courier events
   ↓
Delivery outcome
   ↓
COD receivable
   ↓
Payout + adjustments
   ↓
Reconciliation
   ↓
Immutable money ledger
   ↓
Profit snapshot
   ↓
Money / Insights / Alert
```

Nothing may bypass this chain for convenience.

---

# 145. Final product promise

CODPilot's promise is not:

> "We integrate every courier."

It is:

> **"আপনার COD-এর টাকা কোথায় আছে, কোনটা মেলেনি, আর প্রতিটি অর্ডারে আসলে কত লাভ হচ্ছে—আপনি জানবেন।"**

Courier integrations are inputs.  
The durable product is the seller's financial truth layer.

---

# 146. Final instruction to Claude Code / Codex

> Read this file completely before making architectural changes. Preserve every V1 feature. Build in phases. Do not invent external APIs. Financial and tenant-isolation correctness outrank development speed. Every external integration must degrade safely. Every money-changing operation must be idempotent, traceable and tested. Every historical financial correction must be auditable. If an external capability cannot be verified, implement the defined fallback rather than pretending it exists. The app is complete only when a real seller can go from order → courier → delivery/return → COD receivable → payout → reconciliation → profit with correct money and no duplicate external action.

---

# 147. Current official-reference notes rechecked for this final version

The following external assumptions were rechecked while producing this version and must still be rechecked at implementation/release time because policies/APIs can change:

1. **Google Play:** current Play policy/help states that Play-distributed apps selling digital app functionality/cloud/business-productivity subscriptions generally use Google Play Billing unless an applicable exception/program applies. Current external-payment-link help surfaced for Japan, not Bangladesh.
2. **bKash:** official business pages list payment gateway, tokenized checkout and subscription payments. Exact merchant APIs/credentials are contract/onboarding dependent.
3. **Steadfast:** official public site confirms COD, real-time tracking, merchant operations, current public delivery pricing examples and active API-created parcels; exact merchant API endpoints/capabilities must be verified from the merchant's current API documentation/account before implementation.
4. **Pathao:** keep the previously verified auto-address design decision, but re-check the current merchant API docs when implementing the adapter.

Official references used in verification:
- https://support.google.com/googleplay/android-developer/answer/9858738
- https://support.google.com/googleplay/android-developer/answer/10281818
- https://support.google.com/googleplay/answer/16805335
- https://www.bkash.com/en/business
- https://www.bkash.com/en/page/tokenized_checkout
- https://www.bkash.com/page/subscription-payment-terms-conditions
- https://steadfast.com.bd/
- https://steadfast.com.bd/terms-and-condition-in-bangla
- https://pathao.com/bn/blog/api-merchant-auto-address-feature/
