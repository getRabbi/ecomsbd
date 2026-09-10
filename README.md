# ecomsbd

**A Bangladesh-first F-commerce seller financial operations system.**

> কুরিয়ারের কাছে কত টাকা আছে, কোনটা মেলেনি, আর প্রতিটি অর্ডারে আসলে কত লাভ —
> আপনি জানবেন।

ecomsbd is not a store builder, an ERP, an accounting suite, a payment processor
or a delivery company. Its job is to stop Bangladeshi Facebook sellers from
losing money on COD:

```
Order → Courier → Delivery / Return / Partial delivery → COD receivable
      → Payout → Reconciliation → Immutable ledger → Profit → Money / Insights / Alerts
```

**Canonical repository:** <https://github.com/getRabbi/ecomsbd.git>

**Current phase: F (billing, entitlements and hardening) — complete.** See
[`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) for exactly what
works today. Products, stock, customers, orders, the paste parser, imports,
offline sync, COD receivables, the financial ledger, payouts, the
reconciliation engine, profit snapshots, return economics, expenses, the
alerts and the Friday summary are in — and now subscriptions, entitlements,
usage metering, RBAC, the ops console, repair tooling, exports, account
deletion and a verified backup restore. Every screen reads real data — there is
no fixture left in the app to fall back to.

**It cannot be released yet, and that is a configuration problem rather than a
software one.** [`docs/RELEASE_READINESS.md`](docs/RELEASE_READINESS.md) lists
nine blockers, every one of them something an operator supplies: a package id,
a signing key, a courier, a billing provider, an SMS gateway, push credentials,
object storage, error tracking and a staging restore drill. Each is refused
honestly in the meantime — nothing pretends to work.

Phase C (the Steadfast adapter) is **blocked** on merchant API documentation, so
parcels move through **manual courier mode**: the seller records that a parcel
went out and later what happened to it. No courier, payment or SMS provider is
integrated, and nothing in this codebase makes a provider HTTP call.

---

## Architecture

```
apps/mobile_flutter/     Flutter (Android-first) — Riverpod, Drift, offline outbox
backend/                 FastAPI modular monolith — SQLAlchemy 2 async, Alembic, ARQ
  app/core/              config, context, logging, errors, crypto, time, ids
  app/db/                portable column types, declarative base, TENANCY GUARDS
  app/common/            money, phone, outbox, audit, feature flags, idempotency
  app/auth/              phone-OTP, sessions, rotating refresh tokens, devices
  app/tenants/           shops, membership, RBAC matrix
  app/entitlements/      plan catalogue, subscriptions, server-side gating
  app/products/          products + the append-only stock movement ledger
  app/customers/         phone-first CRM, encrypted numbers, addresses
  app/orders/            orders, items, numbering, duplicates, paste parser
  app/consignments/      consignment + consignment_item schema (no provider yet)
  app/imports/           CSV templates, dry-run validation, commit
  app/sync/              offline mutation intake + the change feed
  app/ledger/            the append-only financial ledger
  app/money/             COD receivables and aging
  app/payouts/           payouts, statement parsing, source preservation
  app/reconciliation/    candidate scoring, matching, cases
  app/profit/            charge snapshots, the profit engine, P&L revisions
  app/expenses/          ad spend and its explicit allocation to parcels
  app/notifications/     the alerts, the notification centre, the Friday summary
  app/analytics/         the figures behind Home and Insights
  app/couriers/          adapter Protocol + provider capability manifests
  app/api/               middleware, dependencies, error handlers, /v1 routers
  app/worker/            ARQ bootstrap + outbox dispatcher
docs/ADR/                architecture decision records
docs/provider_notes/     provider capability manifests (verified vs UNVERIFIED)
docs/CODPILOT_*.md/html  the frozen master specification and UI prototype
infra/docker/            backend image
```

**Data:** PostgreSQL (Supabase as the managed host) · Redis · Cloudflare R2
**Runtime:** Docker Compose on a single VPS to start

### Invariants this codebase enforces

| Rule | Where |
|---|---|
| Money is integer **paisa**, never a float | `app/common/money.py`, `Paisa` column type, a test that scans the whole schema for `Float` |
| Every business record is tenant-scoped, centrally | `app/db/tenancy.py` — four guards on the ORM session |
| Timestamps are UTC; business dates are Asia/Dhaka `DATE` | `app/core/clock.py`, `TZDateTime` |
| Side effects are transactional | `app/common/outbox.py` — business row + event in one transaction |
| Money-touching requests are idempotent | `app/common/idempotency.py` |
| Secrets and phone numbers never reach a log | `app/core/redaction.py`, applied in the log formatter |
| An unverified provider capability is treated as unsupported | `app/couriers/capabilities.py` |
| Stock changes by recording a movement, never by setting a total | `app/products/service.py` — one relative UPDATE carrying the oversell condition |
| An order's cost is snapshotted when it is placed | `app/orders/service.py` — `unit_cost_snapshot_paisa` |
| Saving an order contacts no courier | there is no provider HTTP call anywhere in the codebase |
| A duplicate order warns; it never blocks | `app/orders/duplicates.py` |
| An import never coerces a bad value into an importable one | `app/imports/templates.py` |
| A queued offline mutation replays to the same record | `app/sync/service.py` — idempotent by mutation id |
| A ledger entry is never edited; a correction is a reversal | `app/ledger/service.py` — four public methods, no update path, asserted by a test |
| One payout line cannot settle more than its own amount | `payout_lines` check constraint |
| Settled can never exceed collectible plus adjustments | `cod_receivables` check constraint |
| Delivered is not paid | separate state machines for the consignment and the receivable |
| A soft-signal match is never applied automatically | `app/reconciliation/scoring.py` — the threshold equals one exact reference |
| An unknown deduction stays visible as unknown | `AdjustmentType.UNKNOWN_DEDUCTION`, never reclassified |
| A profit figure changes by revision, never by edit | `app/profit/service.py` — the previous snapshot is kept and pointed at its successor |
| A weaker source cannot silently replace a better one | `app/profit/service.py` — a downgrade without a reason is refused (section 85) |
| An estimate never renders as exact | `ProfitQuality` on every snapshot, carried through the API to the badge on screen |
| An expense reaches an order only through a named allocation | `app/expenses/service.py` — recording changes no figure |
| Money split across parcels loses no paisa | `Money.allocate` — largest remainder, asserted against the expense total |
| The same alert on the same day is one row | `notifications` unique `(tenant, kind, dedupe_key)` |
| No ranking is shown before the sample supports it | `MIN_RANKING_SAMPLE` in `app/notifications/alerts.py`, and the rule is visible on screen |
| A COD arrival date comes from this shop's own history or not at all | `app/analytics/service.py` — no provider settlement calendar is assumed |

---

## Local setup

Requires **Python 3.12**, **Flutter 3.44+**, and Docker (for PostgreSQL and Redis).

### 1. Services

```bash
docker compose up -d postgres redis
```

Not required for the test suite — it runs on SQLite by default.

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on Unix
pip install -e ".[dev]"

cp ../.env.example .env         # then set DATABASE_URL / REDIS_URL
alembic upgrade head
uvicorn app.main:app --reload
```

API at <http://localhost:8000>, interactive docs at `/docs` (disabled in production).

### 3. Worker

```bash
cd backend
arq app.worker.main.WorkerSettings
```

The worker **requires** `REDIS_URL`. The API falls back to an in-process cache
in local development; a worker without Redis would silently process nothing, so
it refuses to start.

### 4. Flutter

```bash
cd apps/mobile_flutter
flutter pub get
dart run build_runner build      # generates the Drift database (schema v2)
flutter run --dart-define=ECOMSBD_API_BASE_URL=http://10.0.2.2:8000
```

`10.0.2.2` is the Android emulator's alias for the host machine. On a physical
device use your machine's LAN address.

---

## Environment variables

Full list with comments in [`.env.example`](.env.example). The ones without
which nothing works:

| Variable | Notes |
|---|---|
| `APP_ENV` | `local` \| `test` \| `staging` \| `production` |
| `DATABASE_URL` | `postgresql+asyncpg://…`. SQLite is refused in production. |
| `REDIS_URL` | Optional in local/test, **required** in staging/production and for the worker |
| `JWT_SIGNING_KEY` | ≥ 32 chars. `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `CREDENTIAL_ENCRYPTION_KEY` | base64 of exactly 32 random bytes — the courier/billing vault |
| `PHONE_SEARCH_HMAC_KEY` | **Separate** secret for the phone lookup HMAC (master spec §133) |
| `OTP_HASH_SECRET` | Pepper for OTP hashing |
| `OTP_PROVIDER` | `dev_console` \| `sms_gateway` |

Local and test environments boot with working placeholder secrets. **Startup
fails fast** in staging and production if any of them is still a placeholder.

---

## Migrations

```bash
cd backend
alembic upgrade head                                   # apply
alembic downgrade -1                                   # roll back one
alembic revision --autogenerate -m "description"       # draft from model changes
alembic history --verbose
```

Autogenerate drafts a migration; it does not review one. Read the generated file,
especially anything touching a money column. Rules in `backend/migrations/README`.

The database URL comes from application settings, not `alembic.ini`, so a
migration cannot target a different database than the app reads.

---

## Tests

```bash
# Backend — no services required
cd backend
python -m pytest -q                  # 833 tests
python -m ruff check app tests migrations
python -m ruff format --check app tests migrations
python -m mypy                       # strict

# Backend against real PostgreSQL
TEST_DATABASE_URL=postgresql+asyncpg://ecomsbd:ecomsbd@localhost:5432/ecomsbd_test pytest -q

# Flutter
cd apps/mobile_flutter
flutter analyze
flutter test                         # 179 tests
```

Widget tests render at **360×800** — the reference low-end Android screen — and
assert no layout overflow. `test/performance/` guards the shapes a real shop
reaches: a page off a 2,000-order local mirror, deep paging, search, a
500-product low-stock filter, and search-box debouncing.

The commerce screens run against a fake HTTP adapter rather than a stubbed
repository, so a screen test exercises the same client, interceptors and error
translation the app ships with.

---

## How the commerce core behaves

**Stock.** A seller never types a new total. They record a movement with a
direction and a reason, and the balance is the ledger's answer. The balance
update is a single relative statement carrying its own non-negative condition,
so two adjustments racing cannot lose each other, and going negative requires an
explicit `allow_negative`.

**Orders.** `POST /v1/orders` creates a record in `DRAFT` and contacts nobody.
Courier booking is a separate, explicit action that does not exist yet — an
external create is irreversible, so it is never a side effect of saving. Order
numbers are `CP-YYYYMMDD-NNNN`; the `CP-` prefix is a stored value and is
deliberately not rebranded.

**The paste parser** is deterministic and offline — no model, no network beyond
the call itself. It returns fields for the seller to confirm, with a confidence
score per field; anything it is unsure about comes back empty rather than
guessed, more than one phone number is put to the seller instead of picked, and
the original text is always echoed so a failed parse loses nothing.

**Duplicate detection** looks 24 hours back and separates identity signals (same
phone, same customer) from corroborating ones (identical or similar amount,
similar items). At least one corroboration is required, so a repeat customer is
not flagged simply for existing. It warns; it never blocks.

**Imports** go upload → detect → dry-run → commit. The dry run is not skippable,
re-uploading a committed file is refused by content hash, and a row whose phone
or amount cannot be parsed is reported with the value the file actually
contained — never coerced into something importable.

**Offline.** Orders, products, customers and stock adjustments can be created
and edited with no connection; courier booking, risk lookups and payouts cannot,
and are absent from that path by design. Each queued mutation carries the id the
device minted, which is also the server's deduplication key, so a retry over a
bad connection cannot turn one sale into two parcels. Reads fall back to the
local mirror **labelled with when it was true**; with nothing cached the screen
says "No connection" rather than showing an empty list.

**What the app does not claim to know.** Order cards show courier state, delivery
risk and profit as three separate facts, and until the engines that produce them
exist they read *Not booked*, *Not checked* and *Pending*. A customer with no
terminal history shows "No history yet", not 0%.

---

## How the money core behaves

**Delivered is not paid.** A parcel and the money it owes are separate objects
with separate state machines. A delivery creates a *receivable*; only a payment
matched against it settles anything. Every system that collapses those two ends
up telling a seller their money has arrived when it has not.

**The ledger is append-only.** Every money event is a row, and there is no
update path to it anywhere in the application. A correction is a reversal row
pointing at the original, so both the mistake and its fix survive — which is
what lets anyone answer "why is my outstanding ৳4,200?" three months later. The
Money screen's totals are summed from those rows rather than from maintained
counters, so the dashboard cannot drift from what is behind it.

**A partial delivery collects only what was delivered.** Recording one requires
the per-item quantities; assuming the original COD is refused.

**Statements are evidence, not instructions.** Importing one creates a payout
with a line per row and settles nothing. Re-importing the same file is refused
by content hash. A row whose amount cannot be read is imported carrying the text
the file contained rather than dropped or turned into ৳0, and the source file is
kept so a reconciliation result can be explained later.

**Matching prefers precision over recall.** Only an exact provider reference —
consignment id, tracking code, or order number — settles a parcel on its own.
A matching amount, a plausible delivery date and a matching phone put together
still fall short of the threshold, so an amount-only match can never happen
automatically. A tie between two candidates, or two parcels claiming the same
reference, goes to a person. Every manual match records who made it and why, and
unmatching is a reversal rather than a deletion.

**Shadow mode** runs the whole engine and writes nothing, so a matching rule can
be measured before it is allowed to move money.

**Nothing is folded away.** An unrecognised deduction stays labelled as
unrecognised with the courier's own words attached. An overpayment settles only
what was owed and raises a case for the rest. A write-off is recorded as a loss
rather than quietly leaving the outstanding total.

**Cases, not colours.** The eight problems master spec section 16 names —
delivered but unpaid, underpaid, overpaid, unknown deduction, duplicate line,
unmappable payment, stale in transit, returned but not restocked — are separate
records with an owner and an outcome. Closing one requires a note.

---

## How profit behaves

**A profit figure is a snapshot with a revision, never a number that changes
under you.** A parcel gets one the moment it finishes, and another whenever
something behind it moves — a courier charge arriving, a payout settling, an
expense being allocated. The old revision stays readable with the reason for
the new one beside it, so a seller who read ৳4,200 in March can still see that
৳4,200 in December next to whatever replaced it.

**The app says how sure it is.** Every figure carries whether its inputs were
settled, estimated or missing, and the screens render those differently. A
৳40,000 profit built from twelve estimated parcels and one built from twelve
settled parcels are different claims, and a product that printed them the same
way would be teaching sellers to trust the wrong one.

**A better source wins, and a worse one has to ask.** A settled charge off a
statement replaces a booking estimate silently. A guess arriving after the
statement is refused unless somebody gives a reason, which turns it into an
audited correction rather than a quiet overwrite.

**A sellable return is stock, not a loss.** What a return actually costs is the
courier's fee on a parcel that collected nothing, plus anything genuinely
written off. Booking the returned goods as a loss as well would make every
return look twice as expensive as it was.

**Recording an expense is not applying it.** Entering ৳5,000 of ad spend
changes no profit figure until the seller says which parcels it paid for. The
split loses no paisa, spend that reached nothing stays visible as unallocated
rather than being smeared across unrelated orders, and rent is not pushed down
onto parcels at all — spreading fixed costs per parcel is not accounting, it is
a guess wearing a decimal point.

**Alerts are about money, not activity.** Section 23's four lines — delivered
but unpaid, underpaid, stuck in transit, returned but not restocked — each
carry a count *and* an amount, because the amount is what decides the order a
seller works through them. An alert with nothing in it is never raised, and the
same warning on the same day is one row rather than one per scan.

**A ranking waits for evidence.** Below five parcels, a "best product" or
"worst courier" is noise, so the app says why it is staying quiet instead of
sending a seller to delist something that was merely unlucky. The same rule
governs the COD arrival forecast: a date comes from this shop's own settlement
history with that courier, and money with no such history is labelled as having
no arrival date rather than shown as due.

---

## Development OTP behaviour

⚠️ **The development OTP provider does not send an SMS.** It writes the code to
the application log and returns it in the API response as `debug_code`.

**Anyone who can read the log can sign in as any phone number.**

Three independent guards keep it out of production:

1. `Settings` rejects `OTP_PROVIDER=dev_console` when `APP_ENV=production`;
2. `Settings` also rejects `ALLOW_DEV_OTP=true` and `OTP_EXPOSE_DEBUG_CODE=true`
   there;
3. `DevConsoleOtpProvider` re-checks the environment at construction *and* at
   send time, so it cannot be reached by a path that bypassed config.

The OTP screen displays a loud amber warning whenever the API returns a
`debug_code`, so a build pointed at a real server can never look like this.

Local sign-in: request a code, read `debug_code` from the response (it is
pre-filled in the app), verify.

---

## Security notes

- **Sessions.** Short access tokens (15 min) plus opaque, single-use refresh
  tokens stored hashed. Presenting a rotated token revokes the entire session —
  the only causes are theft or a badly broken client.
- **Phone numbers.** Stored as AES-GCM ciphertext plus a keyed HMAC for exact
  lookup and the last four digits. Never in clear text, never in a log, masked
  as `01712****78` everywhere it is displayed.
- **Courier credentials.** AES-256-GCM with a versioned key id, bound to the row
  they belong to via GCM additional data. Never returned to the client.
- **Logs.** Redaction is applied in the logging formatter, not at call sites, so
  a new module cannot leak a secret by forgetting to scrub it.
- **Cross-tenant access** returns a plain 404 to the client and logs at `ERROR`.
  Returning 403 would confirm that another tenant's record exists.

Do not run this against real seller data until Phase F hardening is complete.

---

## Open decisions

**`PACKAGE_ID_DECISION_REQUIRED`** — the Android `applicationId` is still the
scaffold default `com.example.ecomsbd`. Play rejects `com.example`, and the id is
permanent once published. See the comment block in
`apps/mobile_flutter/android/app/build.gradle.kts`.

**`RELEASE_SIGNING_REQUIRED`** — release builds still use the debug keystore.

Provider integrations blocked on documentation or credentials are listed in
[`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md#blocked--external-verification-required).

---

## Specification

The product and technical source of truth is
[`docs/CODPILOT_CLAUDE_CODE_FINAL_MASTER_SPEC.md`](docs/CODPILOT_CLAUDE_CODE_FINAL_MASTER_SPEC.md);
the visual source of truth is
[`docs/CODPILOT_PREMIUM_REDDIT_GLASS_FINAL.html`](docs/CODPILOT_PREMIUM_REDDIT_GLASS_FINAL.html).
`CODPilot` in those files is a historical working name — the product is
**ecomsbd**.

Where they conflict, precedence is: financial correctness → security → data
integrity → master spec product rules → UI prototype.
