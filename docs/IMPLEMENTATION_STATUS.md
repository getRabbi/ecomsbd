# Implementation status

**Phase C (Steadfast courier integration) — CODE COMPLETE; external
verification pending.** Last updated 2026-09-11.

| | |
|---|---|
| **Phase A** | COMPLETE |
| **Phase B** | COMPLETE |
| **Phase C** | **CODE COMPLETE** — Steadfast V1 documentation supplied and implemented; live credential test pending |
| **Phase D** | COMPLETE |
| **Phase E** | COMPLETE |
| **Phase F** | COMPLETE |
| **Canonical repository** | <https://github.com/getRabbi/ecomsbd.git> |
| **Branch** | `main` |
| **Phase A baseline commit** | `d70856f66ba206d1f1885235110680fbfc577eaf` |
| **Verified at Phase C** | 1003 backend tests **on SQLite and on real PostgreSQL 16**, 203 Flutter tests, ruff + ruff format + mypy --strict + flutter analyze + dart format all clean, migration upgrade/downgrade/upgrade round-trip, and a passing backup restore drill |

Phase F is the first phase whose test suite has been run against real
PostgreSQL rather than only SQLite. That run found 43 failures the SQLite
suite never showed — see "What running on PostgreSQL found" below. **A
SQLite-only green pipeline is not release evidence** (ADR 0004), and CI now
runs both plus a backup restore drill on every build.

`docs/RELEASE_READINESS.md` is the gate. Its summary line is the honest one:
**ecomsbd cannot be released today** — not because the software is incomplete,
but because it is not yet connected to the outside world it needs. Nine
blockers, every one of them configuration an operator supplies.

Phases D–F were built ahead of Phase C because Phase C could not start: there
was no provider documentation to build against, and master spec section 140
forbids inventing it. That changed on 2026-09-11, when Steadfast's V1 API
documentation was supplied. It was read in full, transcribed into
`docs/providers/steadfast/CONTRACT.md`, and implemented against — and the
prediction held: the adapter replaced the entry point and nothing downstream
changed. Manual courier mode remains unconditional and is what every provider
failure degrades to.

What the document does **not** contain shaped the implementation as much as what
it does. It has no webhook section, no error-body schema, no idempotency
statement, no rate limit, no pagination parameter, and no response schema at all
for four of its twelve endpoints. Every one of those is recorded as `UNKNOWN` or
`UNVERIFIED` rather than guessed — see
`docs/providers/steadfast/IMPLEMENTATION.md`.

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

### Billing and entitlements (Phase F)

| Area | Detail |
|---|---|
| **Subscription domain** | All nine states from section 93, with the columns that make a state explainable: cancel-at-period-end, grace window, trial end, provider product id, distribution channel, verification and last-sync timestamps, and a status reason in support's words. `is_current()` owns the trial and grace windows, so no caller can forget one and cut a paying seller off mid-retry. |
| **Billing tables** | `billing_transactions`, `subscription_events`, `billing_provider_customers`, `billing_attempts`, `play_purchase_tokens`, `billing_webhook_events`. The last two are deliberately **not** tenant-owned: replay protection has to see every tenant's tokens, or another shop's replay reads as "unseen" and buys a second entitlement from one payment. |
| **No payment secret is stored** | A purchase token is kept as SHA-256 — enough to recognise a replay, useless to whoever reads the table. |
| **Provider architecture** | `BillingProvider` per section 92, with Play, bKash and admin-manual implementations. Each takes a transport port and **this repository ships no live implementation of either**, so `availability()` returns a blocker code and every live operation refuses. Everything above the port is real and tested against fixtures. |
| **Webhook verification** | Both verifiers **refuse when no secret is configured.** A verifier that returns true with nothing to check with turns the endpoint into an unauthenticated "grant me a subscription" API. |
| **Replay protection** | A token presented twice by the same shop is idempotent; presented by another shop it is refused and audited. A webhook is deduplicated on event id, then reference, then payload fingerprint (section 78), and a replay increments a delivery counter. A renewal sets an **absolute** expiry, so two deliveries cannot buy two months. |
| **Distribution channel** | Section 27.1 in one place. A client may narrow the channel — declaring itself a Play build — but never widen it, and `GET /billing/channel` is the only input to the client's billing CTA. |
| **Usage counters** | Atomic by construction: the quota check lives inside the UPDATE's WHERE clause, so two requests at the boundary cannot both spend the last one. Period keys come from the **tenant's** business date, not UTC. |
| **Enforcement** | Order creation meters `orders_monthly_limit` inside `OrderService`, so the API, offline sync and CSV import all pass one gate. Reconciliation *actions* need the entitlement; advanced analysis needs `advanced_profit`; the profit window refuses rather than silently clamping. |
| **Downgrade keeps everything** | Section 51. A lapsed shop still opens every order, customer, payout, case and profit record it has, and a test asserts each of those endpoints by name. |
| **Dunning** | Configurable grace, retries and past-due. Repeated failure notifications do not extend grace — three identical notifications must not buy nine days. Neither Play's nor bKash's real retry schedule is assumed. |
| **Reconciliation job** | Section 90's missed-notification safety net, twice a day, with expiry running an hour later so provider truth has its chance first. Local expiry alone never revokes a subscription the provider still calls active. |
| Performance | The entitlement gate resolves once per request: five checks cost one subscription read, asserted by counting SQL rather than calls. |

### RBAC, admin and support (Phase F)

| Area | Detail |
|---|---|
| **RBAC is now enforced** | It was defined in Phase A and used almost nowhere. Every mutating seller route now declares the permission it needs, and the team endpoints make non-Owner roles reachable — so the matrix is exercised by tests that sign in as a Packer and get refused. |
| Team management | Add, re-role and remove by phone number. `team_member_limit` is a **standing** cap counted from `tenant_users`: a removed seat frees itself. A shop can never be left without an Owner, and removing someone revokes their live sessions rather than waiting for token expiry. |
| **Platform admin** | A separate authority from shop membership. `X-Admin-Token` resolves to a `platform_admins` row or a configured bootstrap token; a seller token is worth nothing on `/v1/admin` and an admin token is worth nothing on the seller API. With neither configured — the shipped default — there is no console at all. |
| Admin roles | SUPPORT reads and opens cases; OPS repairs and flips flags; only SUPERADMIN can issue plan credit or unmask a phone number. |
| **PII masking** | Masked everywhere by default. The single reveal route takes a reason of at least eight characters, writes the audit entry, **commits it**, and only then returns the number. There is no bulk variant. |
| Support cases | Section 102, linkable to an order, consignment, payout, reconciliation case or subscription. Closing one requires a resolution. |
| Support bundle | Ids, a status timeline and provider state. A test asserts the seller's and the customer's phone numbers are not in its bytes. |
| **Repair tools** | Eleven named actions, each with a declared permission, a mandatory reason, an audit entry and an idempotency key. No generic row editor. `rebuild_money_summary` re-derives the outstanding total and the ledger's receivable bucket independently and **reports** a mismatch rather than "fixing" one to match the other. |
| Honest refusals | `revoke_courier_credential` returns `UNAVAILABLE`, because no courier credential storage exists yet and a silent no-op on a leaked key is worse than an honest refusal. |
| **Provider health** | Per (provider, capability, scope), with a circuit breaker. One failed request never marks a provider down; an auth failure goes straight to `NEEDS_RECONNECT` because it will not fix itself. One shop's bad credential never disables the provider for everyone. |

### Privacy, exports and security (Phase F)

| Area | Detail |
|---|---|
| **CSV formula safety** | `SafeCsvWriter` is the only CSV writer in the codebase, so an export added later cannot forget to escape. A cell whose first non-blank character is `=`, `+`, `-` or `@` gets an apostrophe; embedded newlines are flattened so a cell cannot smuggle a second leading `=` past review. Excel strips leading tabs before deciding, so those are stripped before the check. |
| Export encoding | UTF-8 with a BOM: without it Excel on Windows renders every Bangla name as mojibake. |
| **Upload hardening** | Content sniffing, not the client's MIME type. An XLSX renamed to `.csv` is caught by its ZIP magic number and refused with a sentence a seller can act on, rather than parsed into a payout of nonsense amounts. Both upload paths go through it. |
| **Exports** | Six datasets, tenant-scoped, permission-checked per dataset (customers is Owner-only), plan-gated, and audited separately on request and download. The link is issued once, stored hashed and expires — and is **not sufficient alone**: the download needs the seller's own session too. When it expires the content is dropped and the row stays. |
| **Account deletion** | Section 100's seven steps. Customer names, numbers and addresses are permanently anonymised; the ledger, receivables, payouts and profit snapshots are kept in anonymised form. Scheduled with a 14-day cooling-off window during which everything keeps working. Ownership is re-verified against the membership table. A user who owns another shop keeps it. |
| Provider-side cancellation | Explicitly **not** claimed. Play subscriptions are cancelled in Play and a bKash mandate needs a merchant call this deployment cannot make; the record says so rather than leaving a seller believing they had stopped being charged. |
| Sessions and devices | List, revoke one device (revoking its sessions and dropping its push token), sign out every other device. A device belonging to someone else is a 404, not a 403. Phase A's refresh-reuse detection is re-asserted. |

### Notifications and operations (Phase F)

| Area | Detail |
|---|---|
| **Transports** | `PushTransport` and `SmsTransport`, provider-neutral. Neither has a live implementation and **both say so** — `NOT_CONFIGURED`, never a quiet success. Payload validation runs *before* the configuration check, so a malformed push fails the same way whether or not Firebase is configured. |
| Segment counting | GSM-03.38 / UCS-2, implemented because it is a property of the alphabet rather than of any gateway. It is the measurable reason the templates are Banglish. Labelled an estimate; a provider's own count wins and both are kept. |
| **Delivery records** | One row per (notification, channel, target); the unique constraint is the idempotency. Targets are stored hashed. A permanently rejected token is dropped from the device. A transport that raises is caught — the notification is already in the centre. |
| **Alert fatigue** | Section 95, server-side. `INFO` never interrupts; a muted kind is recorded as `SUPPRESSED` with the reason in words; a per-shop hourly cap stops a runaway job at six pushes. Routine tracking push is opt-**in**. |
| Scheduled work | `reconcile_billing`, `expire_subscriptions`, `dispatch_notifications`, `expire_exports`, `run_account_deletions`, all idempotent and on a system session. |
| **Backup and restore** | `backup.sh`, `restore.sh` and `verify_restore.py`. The restore script refuses any target whose database name does not look like a restore target. Verification checks migration head, every mapped table, the per-tenant receivable/ledger invariant, oversettlement, and that no phone is in clear text. **The drill runs in CI on every build.** |
| Runbooks | Eight, each with detection, containment, the feature flag, data queries, seller impact, recovery and verification. |

---

## What running on PostgreSQL found

Phase F is the first phase whose suite has been run against a real PostgreSQL
rather than only SQLite. The first run: **41 failed, 768 passed.** Three
distinct causes, all now fixed, all with the SQLite suite green throughout.

**1. Pooled connections outliving their event loop (39 failures).**
`pytest-asyncio` gives each test its own event loop while the engine is a
process-wide singleton, so a pooled `asyncpg` connection was handed to a test
whose loop had closed. It surfaced as `RuntimeError: Event loop is closed` a
long way from the cause. The test environment now uses `NullPool` on any
backend, so no connection outlives the loop that created it.

**2. A bind parameter inside a `GROUP BY` (6 failures, a real product bug).**
`_returns_by_area` grouped by `coalesce(area, district, "Unknown")` where the
fallback was a bound parameter. PostgreSQL matches a `GROUP BY` expression to
the select list textually, and a bind renders as `$1` in one place and `$6` in
the other — so the same Python expression became two different SQL expressions
and the server rejected the query. **Every returns-by-area report was broken on
PostgreSQL and worked on SQLite.** The fallback is now a SQL literal.

**3. A test fixture that only worked on the permissive backend (1 failure).**
The profit-snapshot isolation test inserted a snapshot pointing at two random
UUIDs. SQLite does not enforce foreign keys by default; PostgreSQL does. The
fixture now creates a real order and consignment — which is what the test
claimed to be testing all along.

A fourth bug was found by the clock rather than the database, on the same day:
**an expense allocation reached zero parcels for six hours of every day.**
`_eligible_consignments` compared an expense's period — a Dhaka business date —
against `delivered_at.date()`, the UTC date. Dhaka is UTC+06:00, so between
18:00 and 24:00 UTC a seller's "today" is a day ahead of the truncated
timestamp: their ad spend stayed unallocated, no profit figure moved, and
nothing said why. This is precisely the failure master spec section 69 exists
to prevent. Fixed, with a regression test pinned to 19:00 UTC that fails
against the old code.

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

**Billing:** `GET /billing/entitlements` · `GET /billing/plans` ·
`GET /billing/usage` · `GET /billing/subscription` · `GET /billing/channel` ·
`GET /billing/history` · `GET /billing/events` · `POST /billing/play/verify` ·
`POST /billing/restore` · `POST /billing/web/checkout` ·
`POST /billing/web/confirm` · `POST /billing/cancel` ·
`POST /billing/webhooks/{provider}`

**Team:** `GET /team` · `GET /team/roles` · `POST /team` ·
`PATCH /team/{user_id}` · `DELETE /team/{user_id}`

**Account and privacy:** `GET /account/devices` · `GET /account/sessions` ·
`POST /account/devices/{id}/revoke` · `POST /account/logout-others` ·
`GET /account/notification-preferences` ·
`PATCH /account/notification-preferences` · `GET /account/privacy` ·
`POST /account/delete` · `POST /account/delete/cancel`

**Exports:** `GET /exports` · `POST /exports` ·
`GET /exports/{id}/download`

**Admin** (`X-Admin-Token`, excluded from the public schema): `GET /admin/me` ·
`GET /admin/tenants` · `GET /admin/tenants/{id}` ·
`GET /admin/tenants/{id}/billing` · `GET /admin/tenants/{id}/support-bundle` ·
`POST /admin/tenants/{id}/customers/{id}/reveal-phone` · `GET /admin/ops/counts` ·
`GET /admin/ops/webhooks` · `GET /admin/ops/provider-health` ·
`GET /admin/ops/audit` · `GET /admin/flags` · `GET|POST /admin/support-cases` ·
`PATCH /admin/support-cases/{id}` · `GET /admin/repairs` ·
`POST /admin/repairs/{action}`

**Other:** `GET /couriers/providers` · `GET /health/live` · `GET /health/ready`

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
| Courier adapter | Fully implemented for Steadfast V1: typed client, transport, contract-as-data, DTOs, status map, error taxonomy, plus the manual path | Live credential test pending. Pathao and RedX remain manifest placeholders. |
| Provider manifests | Loader, three-valued capability state, per-capability evidence (endpoint, method, models, whether live credentials are needed), named unknowns and blockers | Pathao and RedX are entirely `unknown` pending real documentation. Steadfast is verified against V1 except `webhook`, which stays `unknown` because the document has no webhook section. |
| Subscriptions | The full state model, provider architecture, verification flow, dunning and reconciliation; every transition recorded | No provider **credentials**. Play needs a service account and a decided package id; bKash needs merchant onboarding. Both refuse honestly and neither can grant today. |
| Offline detection | `isOfflineProvider`, the banner, and the sync controller that sets it from the last attempt | No connectivity subscription; the flag follows API results, which is what actually matters |
| Conflict resolution UI | A conflicted record is marked, kept, and never overwritten; the detail screen explains it | No side-by-side "yours / theirs" chooser yet |
| Import formats | CSV, with encoding fallbacks and delimiter sniffing, for both products/orders and courier statements | XLSX is detected and refused with a clear message rather than mis-parsed. No provider statement format is verified, so the statement reader is generic and the seller confirms the column mapping. |
| Payout source storage | The statement is retained in full, with its SHA-256; generated exports too | Cloudflare R2 is not provisioned, so `storage_key` is null and the content lives in the database row. Moving it is a migration, not a redesign. |
| Provider payment API | Steadfast `/payments` and `/payments/{id}` are integrated end to end: dedupe by provider payment id, raw retention, payout + lines + adjustments, and hand-off to the existing reconciliation engine | Those two endpoints have **no documented response schema**, so the typed field names are inferred and marked `UNVERIFIED` until one live call confirms them. Statements and manual entry remain the working paths for other couriers. |
| Notifications | The centre, the four alerts, the Friday summary, **and** the transport contracts, delivery records, retry policy, preferences and alert-fatigue rules | No push or SMS **provider**. Every attempt is recorded as `NOT_CONFIGURED` rather than as a quiet success, and section 94's rule that push is only ever a second copy means the product works without it. |
| Courier scorecard | Delivery success and return rate per courier from the shop's own history, with the sample rule visible | Section 24's median delivery time, effective cost per delivered parcel and settlement lag need per-provider timing data that only accumulates once real bookings exist |

---

## Not started

Everything below is in the V1 spec and is scheduled, not dropped.

**Blocked on external configuration, not on engineering:** Play Billing
verification, the bKash merchant client, SMS delivery, FCM push, R2 object
storage and Sentry. Every one has its domain, its tests and its transport
port in place, refuses honestly today, and needs only credentials. See
`docs/RELEASE_READINESS.md`.

**V1.1 / V2:** Pathao, RedX, team UI, web dashboard, product variants, bulk
risk, courier scorecards, cashflow forecast, customer campaigns.

---

## Blocked / external verification required

Nothing below can be implemented from the specification alone. Each needs
something only the operator can supply. Master spec sections 61 and 140 forbid
inventing any of it.

| Item | Blocked on | Consequence today |
|---|---|---|
| **Android `applicationId`** | **An operator decision.** Neither the spec nor the prototype names one. Currently the scaffold default `com.example.ecomsbd`, which Play rejects — and the id is permanent once published. | `PACKAGE_ID_DECISION_REQUIRED` in `android/app/build.gradle.kts`. Blocks any Play upload and all billing work. Phase F **checked the repository for an official decision and found none**, so nothing was invented: the Play provider reports this blocker by name and refuses. |
| Release signing keystore | Operator | Release builds use the debug key |
| **Steadfast API** | A merchant account. The documentation blocker is **resolved** — V1 was supplied 2026-09-11 and implemented. | Everything is built and tested against contract fixtures. `STEADFAST_LIVE_CREDENTIAL_TEST_REQUIRED`: no real key has authenticated, no real parcel has been created, and four endpoints' response schemas are inferred rather than observed. |
| **Steadfast webhook** | A webhook contract from Steadfast — the V1 document has no webhook section at all | `STEADFAST_WEBHOOK_CONTRACT_REQUIRED`. The receiver, replay protection, verifier/parser ports and queue are built and tested; the verifier refuses everything, and polling is the complete V1 sync path. |
| Pathao API | Current merchant API documentation | Auto-address design decision is recorded and verified; endpoints are not |
| RedX API | Any documentation | Manifest placeholder only |
| bKash | Merchant onboarding contract | Checkout records, callback verification, the recurring/one-time split, dunning and cancellation are implemented; no endpoint path or signature scheme is guessed. `BKASH_MERCHANT_SETUP_REQUIRED`. |
| Google Play Billing | Package name + service account; policy re-check at release | Everything except the HTTP call is implemented and tested against fixtures: replay protection, tenant binding, package/product validation, plan mapping, acknowledgement, the state machine and RTDN parsing. `PLAY_BILLING_EXTERNAL_CONFIGURATION_REQUIRED`. |
| SMS gateway | Provider choice, sender ID, encoding/segment cost rules | Transport, segment estimation and per-segment quota metering are in place. **This is the blocker that stops even a pilot**: production refuses to start with the development OTP provider, so nobody can sign in. `SMS_PROVIDER_REQUIRED`. |
| FCM | Firebase project credentials | Transport contract, payload validation and delivery records are in place; every attempt records `NOT_CONFIGURED`. `FCM_CREDENTIALS_REQUIRED`. |
| Cloudflare R2 | Bucket + keys | Statements and exports live in database rows until then. `R2_CREDENTIALS_REQUIRED`. |
| Sentry | DSN | Wired and inert, with `send_default_pii=False` and a redaction hook. `SENTRY_CONFIGURATION_REQUIRED`. |
| **Staging restore drill** | A staging environment | A real drill passed on PostgreSQL 16.13 and runs in CI on every build, but not against production-shaped data — so the stated RTO is an estimate. `STAGING_RESTORE_DRILL_REQUIRED`. |
| Risk data source | A licensed provider or courier customer-stats capability | Risk check tile is visibly disabled rather than fake; order cards read "Not checked" |
| **Provider statement format** | One real Steadfast/Pathao/RedX payout statement | The statement reader is generic and marked `UNVERIFIED`. It works, and the seller confirms the column mapping, but no provider-specific profile can be written without a real file. |

---

## Test coverage

| Suite | Count | Command |
|---|---:|---|
| Backend (SQLite) | 1003 | `cd backend && pytest -q` |
| Backend (**PostgreSQL 16**) | 1003 | `TEST_DATABASE_URL=postgresql+asyncpg://… pytest -q` |
| Flutter | 203 | `cd apps/mobile_flutter && flutter test` |
| Migration round-trip | passing | `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` |
| Restore drill | passing | `infra/backup/restore.sh <dump>` |

**Phase C added 170 backend and 24 Flutter tests.** The backend ones are grouped
by the claim they defend rather than by module: the Steadfast contract and
adapter (78), booking and `BOOKING_UNKNOWN` recovery (27), status/return/payment
sync and the webhook receiver (32), courier accounts and credential handling
(17), and tenant/permission/PII/concurrency safety (16). The Flutter ones cover
the connection screen's four validation outcomes, the ambiguous-booking screen
that deliberately offers no retry, bulk partial success, and the payout screen's
honesty about an inferred schema.

**Backend (833):** billing verification, replay and dunning (53), platform
primitives — outbox, idempotency, crypto,
redaction, flags, rate limiting, time, audit (45), reconciliation and the ten
golden financial scenarios (39), phone normalization (37), orders and duplicate
detection (35), analytics and expense endpoints over HTTP (34), tenant
isolation (33), alerts and the Friday summary (33), profit snapshots and
charges (29), payouts and statement parsing (29), imports and sync (29), COD
receivables and delivery outcomes (26), money rules (26), products and the
stock ledger (25), money endpoints over HTTP (24), auth flow over HTTP (23),
parser (22), customers (19), the financial ledger (17), expenses and ad
allocation (17), config and production guards (16), onboarding and
entitlements (14), migrations and schema invariants (11). Phase F adds:
admin, RBAC, repairs and provider health (47), entitlements, usage counters
and downgrade safety (43), transports, delivery and alert fatigue (35),
CSV/upload/export/session/deletion security (55), team management and role
enforcement (16).

**Flutter (179):** billing, settings and privacy screens (19), Home,
Insights, expenses and notifications (19), commerce
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

- ~~No PostgreSQL-specific path is exercised by default.~~ **Done in Phase F.**
  The whole suite now passes against PostgreSQL 16.13, and CI runs it there
  as well as on SQLite. Doing it for the first time found three bugs — see
  "What running on PostgreSQL found".
- All ten golden financial scenarios are covered (section 54). What is *not*
  covered is a real provider statement: no courier's format has been verified,
  so the parsing tests use generic CSVs.
- No integration test for the outbox worker loop end to end (needs Redis).
- No screenshot/golden tests for the UI.
- No test drives a real device's file picker; the import screen takes its
  picker as a parameter and the tests supply a CSV directly.
