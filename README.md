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

**Current phase: A (foundation).** See [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)
for exactly what works today. Phase A is a foundation, not a shippable product —
no courier, payment or SMS provider is integrated yet.

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
dart run build_runner build      # generates the Drift database
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
python -m pytest -q                  # 187 tests
python -m ruff check app tests migrations
python -m ruff format --check app tests migrations
python -m mypy                       # strict

# Backend against real PostgreSQL
TEST_DATABASE_URL=postgresql+asyncpg://ecomsbd:ecomsbd@localhost:5432/ecomsbd_test pytest -q

# Flutter
cd apps/mobile_flutter
flutter analyze
flutter test                         # 74 tests
```

Widget tests render at **360×800** — the reference low-end Android screen — and
assert no layout overflow.

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
