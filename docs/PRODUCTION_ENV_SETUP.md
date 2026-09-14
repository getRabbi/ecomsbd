# Production environment setup

**Auth/deployment updated 2026-09-13.**

This is the operator's manual for `.env.production.local`. Work through it
provider by provider: each entry says what the value is for, whether it is
required, whether it is a secret, what shape it has, **where to obtain it**,
where to paste it, and how to check it worked.

```bash
cp .env.production.example .env.production.local     # gitignored
```

Check your progress at any time. It never prints a value, so it is safe to run
on a shared screen:

```bash
cd backend
python -m app.check_production_config --env-file ../.env.production.local --env production
```

or, from the repository root:

```bash
make config-check                              # defaults to .env.production.local
make config-check f=.env.production.example    # or any other file
```

---

## Operator checklist

- [ ] Core app secrets generated
- [ ] PostgreSQL/Supabase configured
- [ ] Redis configured
- [ ] Email provider configured
- [ ] Google Auth configured
- Apple Auth: deferred until iOS; not an Android launch requirement
- [ ] Android package ID finalized
- [ ] Android release signing configured
- [ ] Steadfast merchant smoke test completed
- [ ] R2 configured
- [ ] Firebase/FCM configured
- [ ] Sentry configured
- [ ] Google Play Billing configured
- [ ] bKash configured or intentionally disabled
- [ ] Admin credentials provisioned
- [ ] Production URLs/CORS configured
- [ ] Backup destination verified
- [ ] Staging restore drill passed

Only the first three plus **sign-in** are required to start the API. Everything
else degrades honestly: the affected feature reports itself unconfigured rather
than pretending to work. `docs/RELEASE_READINESS.md` says which of these are
release blockers and which are not.

---

## Read this before you start

### 1. Configure Supabase seller authentication

Supabase Auth owns email/password, Google, Apple and sessions. FastAPI owns all
business APIs and authorization. Phone OTP is disabled. See sections 5–8.

### 2. BOOT CONFIG is not a RUNTIME FEATURE FLAG

Two different mechanisms, and mixing them up is the most common way a
production change goes wrong here.

| | **Boot config** (`.env.production.local`) | **Runtime feature flag** (`feature_flags` table) |
|---|---|---|
| Read | once, at process start | on every request |
| Changing it | needs a restart | takes effect immediately |
| For | credentials, URLs, keys, and safety gates that must be settled before the process serves traffic | product rollout: turning a provider on for one shop, or off for everyone during an outage |
| Examples | `DATABASE_URL`, `SUPABASE_URL`, `STEADFAST_WEBHOOK_ENABLED` | `steadfast_enabled`, `play_billing_enabled`, `bkash_web_billing_enabled`, `pathao_enabled`, `redx_enabled` |

**There is no `STEADFAST_ENABLED` environment variable, and there should not
be.** Steadfast rollout is the `steadfast_enabled` runtime flag, off by
default, so a courier outage is one API call away from being contained rather
than a redeploy. Same for `play_billing_enabled` and `bkash_web_billing_enabled`.

Read the current flags:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://api.yourdomain.com/v1/admin/flags
```

`STEADFAST_WEBHOOK_ENABLED` **is** an environment variable, and that is the
exception that proves the rule: it is not a rollout control but a safety gate
on an endpoint with no verified signature contract. A runtime flag could turn
it on; boot configuration cannot, because startup refuses it.

### 3. How the file reaches the running process

`.env.production.local` lives at the repository root, where you fill it in. The
backend reads `.env` **relative to its own working directory** (`backend/`), so
the file does not load itself. Pick one of these and be consistent:

- **Container / orchestrator (recommended).** Hand the file to the runtime as
  an env file — `env_file:` in Compose, `envFrom` with a Secret in Kubernetes,
  or your platform's secret store. The values arrive as real environment
  variables, and nothing is written to the image.
- **Plain host.** Copy it to `backend/.env` on the server, owned by the service
  user, mode `600`. `backend/.env` is gitignored and CI refuses it if tracked.
- **Ad hoc, for one command.** `--env-file` is understood by the config check
  and by most process managers; real environment variables always win over
  anything in a file.

Whatever you choose, the checks below read the same file you deploy — run them
against the file the server will actually get, not a copy that has drifted.

### 4. Where secrets live, and where they do not

| Secret | Lives in | Never in |
|---|---|---|
| Database password, crypto keys, R2 keys, Apple `.p8`, Play/FCM service accounts, email API key | `.env.production.local` on the server, or your secret manager | git; the Flutter app; a log line |
| Each seller's Steadfast `Api-Key` / `Secret-Key` | the `courier_accounts` table, AES-256-GCM encrypted per row, entered by the seller in the app | `.env` of any kind; a response body; an audit row |
| Admin access | `platform_admins` rows, matched by keyed hash | a shared token, except to bootstrap the first admin |
| `google-services.json` | `apps/mobile_flutter/android/app/` | an environment variable |

Flutter receives public Supabase configuration and stores seller sessions securely.
It uses Supabase Auth for identity and FastAPI for all business APIs. Anything
passed with `--dart-define` is compiled into the APK and can be read out of it;
see `apps/mobile_flutter/env/README.md`.

---

## 1. Core app

### `APP_ENV`

| | |
|---|---|
| **Purpose** | Selects every production guard in `backend/app/core/config.py`. |
| **Required** | Yes |
| **Secret** | No |
| **Format** | `production` |
| **Where to obtain** | You choose it. `local`, `test`, `staging`, `production`. |
| **Where to paste** | `.env.production.local` |
| **How to verify** | The config check header prints `APP_ENV=production`. |

### `DEBUG`

Must be `false`. Staging and production refuse to start otherwise. Not secret.

### `PUBLIC_BASE_URL`

| | |
|---|---|
| **Purpose** | The API's own public origin. Used where the backend needs to name itself. |
| **Required** | Yes |
| **Secret** | No |
| **Format** | `https://api.yourdomain.com` — scheme and host, no trailing slash |
| **Where to obtain** | Your DNS/hosting provider, once the domain points at the API. |
| **How to verify** | Production refuses `http://` and refuses a localhost host, so a wrong value fails the boot rather than being discovered later. |

### `PUBLIC_WEB_URL`, `SUPPORT_EMAIL`

Optional. `PUBLIC_WEB_URL` is reserved for a separate web client. Business email may use
`PUBLIC_BASE_URL`, where the backend serves verification and password-reset pages.
`SUPPORT_EMAIL` is the reply-to on outbound mail and the address shown to a
seller who needs help. Use a mailbox a person actually reads.

### `DEFAULT_TIMEZONE`

`Asia/Dhaka`. Business dates, the Friday summary and every COD ageing window
are computed in it. Do not change it without reading master spec section 69.

---

## 2. PostgreSQL / Supabase

Supabase PostgreSQL remains the existing business database. Flutter uses only
Supabase Auth; all business requests go to FastAPI. Migration `d73e9c5a1201`
revokes client-role business-table privileges and enables RLS without granting
client policies. The backend connects as the existing database owner.

### `DATABASE_URL`

| | |
|---|---|
| **Purpose** | The one database. Every business record lives here. |
| **Required** | Yes — startup fails without it |
| **Secret** | **Yes** — it contains the database password |
| **Format** | `postgresql+asyncpg://USER:PASSWORD@HOST:PORT/postgres` |

**Where to obtain**

1. Supabase dashboard → your project → **Connect** (or Settings → Database).
2. Choose the **connection pooler** string, not the direct connection. Serverless
   and container deployments open and close connections faster than Postgres
   likes; the pooler is what absorbs that.
3. Supabase gives you a `postgresql://...` URI. **Rewrite the scheme** to
   `postgresql+asyncpg://` — SQLAlchemy picks its driver from the scheme, and
   the app is async throughout.
4. Strip any `?sslmode=require` query parameter. `asyncpg` does not accept it as
   a URL parameter; use `?ssl=require` to require TLS explicitly.
5. URL-encode the password if it contains `@`, `:`, `/` or `#`.

**Pooler modes.** Supabase offers two ports:

| Port | Mode | Use for |
|---|---|---|
| `6543` | transaction | the API and the worker — this is the one you want |
| `5432` | session | `alembic upgrade head`, and anything needing prepared statements |

Transaction pooling does not support prepared statements, and asyncpg uses them
by default. If you see `prepared statement "__asyncpg_..." already exists`, you
are on `6543` — either run migrations against `5432` instead, or set
`DATABASE_POOL_SIZE`/`DATABASE_MAX_OVERFLOW` low and use the session pooler for
everything. Run migrations through the **session pooler (5432)**; that is the
simplest arrangement that works.

**How to verify**

```bash
cd backend
DATABASE_URL='postgresql+asyncpg://...5432/postgres' alembic upgrade head
curl https://api.yourdomain.com/health/ready     # checks["database"]["migration"] is set
```

### `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT_SECONDS`

Optional. Keep `pool_size + max_overflow` comfortably below the pooler's
per-client connection limit, and remember the worker opens its own pool.

### `DATABASE_ECHO`

Must stay `false`. It logs statements *with their parameters*, which includes
customer data.

### Backups

Supabase takes automated backups on paid plans; check your project's retention
and set it deliberately. `infra/backup/backup.sh` and `restore.sh` are the
repository's own path, and `infra/backup/verify_restore.py` is what makes a
restore claim true rather than assumed. Master spec section 48: do not claim
backup safety until a restore has been tested.

---

## 3. Redis

### `REDIS_URL`

| | |
|---|---|
| **Purpose** | Rate limits, idempotency keys, distributed locks, and the ARQ job queue. |
| **Required** | Yes outside local/test |
| **Secret** | **Yes** — it contains the Redis password |
| **Format** | `rediss://:PASSWORD@HOST:PORT/0` (TLS) or `redis://...` |

**Where to obtain.** Any managed Redis: Upstash, Redis Cloud, DigitalOcean, or
your own instance. What matters is that the API and the worker reach the *same*
one — a worker on a different Redis silently processes nothing.

**TLS.** Use `rediss://` unless the hop to Redis is inside a private network.
The config check warns when production uses plain `redis://`.

**Who needs it**

- **API** — rate limiting, idempotency, locks. Startup refuses to run a
  deployed environment without it. (Locally it falls back to an in-process
  cache, which is why local development needs no Redis.)
- **Worker** — refuses outright. `app/worker/main.py` raises rather than start,
  because a worker that cannot see the queue would report itself healthy while
  processing nothing: courier status polling, payment sync, outbox dispatch,
  alerts and the weekly summary would all stop, silently.

**How to verify**

```bash
curl https://api.yourdomain.com/health/ready      # checks["redis"]["status"] == "ok"
cd backend && arq app.worker.main.WorkerSettings  # starts and logs "ecomsbd worker started"
```

---

## 4. Business security keys

Preserve independent `CREDENTIAL_ENCRYPTION_KEY` (32 random bytes, base64) and
`PHONE_SEARCH_HMAC_KEY` (at least 32 characters). They protect courier credentials
and CRM/customer lookup. Preserve `JWT_SIGNING_KEY` as well: its legacy name is
retained because existing platform-admin token hashes depend on it. It remains
a backend-only admin requirement and cannot issue production seller sessions.
`OTP_HASH_SECRET` is no longer required for deployed seller auth.

## 5. Supabase Auth

Set `SUPABASE_URL` to the existing project's HTTPS origin and `SUPABASE_ANON_KEY`
to its public anon/publishable key in the backend and Flutter public build config.
FastAPI derives `<SUPABASE_URL>/auth/v1` and audience `authenticated`, verifies
ES256/RS256 JWTs using a bounded ten-minute JWKS cache, and resolves the verified
UUID subject through `auth_identities(provider=SUPABASE, provider_subject=sub)`.
The existing unique constraints enforce one external identity per internal user.
First-login linking requires both emails verified and exactly one existing owner;
ambiguous or unverified collisions require support to verify ownership.

Supabase Dashboard actions:

1. Authentication → Sign In / Providers: enable Email/password and Confirm email;
   disable Phone, anonymous sign-ins and Facebook. Keep Google/Apple enabled once
   their real provider configuration is supplied.
2. Authentication → JWT Signing Keys: create/activate an asymmetric ES256 or RS256
   key if the project still uses legacy HS256. Follow Supabase's key-rotation
   waiting periods. The backend never accepts the legacy shared-secret algorithm.
3. Authentication → URL Configuration: Site URL `https://scalemyprints.com`;
   allow exactly `com.smply.app://auth/callback` as a redirect URL. Flutter uses
   PKCE, encrypted session/code-verifier storage, and this Android intent filter.
4. Settings → API Keys: copy the public key to the backend and Flutter. Put
   `SUPABASE_SERVICE_ROLE_KEY` only in the backend secret group for the retryable
   final account-deletion operation. Ordinary login/JWT verification needs no
   admin key. Deletion preserves the existing grace period and other shops.

The legacy FastAPI auth issuer endpoints return 410 in deployed environments.
Shop selection and logout retain business routing/revocation endpoints; neither
issues credentials. Supabase owns refresh, logout and auth email workflows.
JWTs expire normally after Supabase logout; FastAPI logout also revokes its
session routing row immediately. No custom production token path remains.

## 6. Google via Supabase

Authentication → Providers → Google: supply the Web OAuth client ID and client
secret; include allowed native client IDs as required by Supabase. Configure
Google's authorized redirect URI to the project's `/auth/v1/callback` URL shown
in Supabase. Register Android `com.smply.app` and its debug/release/Play signing
fingerprints in the same Google project. Set Flutter `GOOGLE_CLIENT_ID_WEB`
(`serverClientId`); supply `GOOGLE_CLIENT_ID_IOS` only for a provisioned iOS app.
Flutter exchanges the native ID token with Supabase `signInWithIdToken`.
See [Supabase Google setup](https://supabase.com/docs/guides/auth/social-login/auth-google).

## 7. Apple via Supabase

**Deferred until iOS.** Apple credentials are not Android launch blockers. The
implementation is retained, and the Android login UI shows Email and Google only.
The instructions below apply when Apple sign-in is activated for a later release.

Authentication → Providers → Apple: register the native bundle ID in Client IDs
for native ID-token login. A hosted OAuth flow additionally needs the Apple
Services ID and OAuth secret (JWT generated with Team ID, Key ID and the `.p8`
private key); configure the Supabase callback in Apple's Services ID and renew
the OAuth secret within six months. Native Apple login uses a hashed nonce with
the original nonce supplied to Supabase. Android opens Supabase's hosted Apple
OAuth flow in the external browser and exchanges the callback through the existing
PKCE handler. Allow `com.smply.app://auth/callback` in Supabase and put the Apple
Services ID first in the provider Client IDs. Apple activation for the later iOS work requires that
provider configuration. An iOS release still requires its native
runner, signing and Sign in with Apple capability.
See [Supabase Apple setup](https://supabase.com/docs/guides/auth/social-login/auth-apple).

## 8. Supabase SMTP and independent business email

Authentication → Email → SMTP Settings: enable custom SMTP and provide Host,
Port, Username, Password, Sender email and Sender name. Verify the sender domain
with the SMTP provider, save the settings, then verify confirmation and recovery
delivery to a real inbox. No SMTP configuration or delivery is assumed here.
Authentication → Email Templates: use the Supabase confirmation URL, preserving
the configured redirect; do not point mail to the retired FastAPI token pages.

FastAPI `EMAIL_TRANSPORT=disabled` is valid for production auth. Its optional
`EMAIL_API_KEY`/sender settings are exclusively for independent business mail.
Resend can also supply SMTP credentials in Supabase; do not place them in Flutter.

## 9. Steadfast

Phase C is code complete. What remains is a merchant account, not engineering.

### There is no merchant credential in this file

| | |
|---|---|
| **Platform config** (env) | `STEADFAST_BASE_URL`, `STEADFAST_BULK_CHUNK_SIZE`, `STEADFAST_WEBHOOK_ENABLED`, the `COURIER_*` timeouts and schedules |
| **Merchant credentials** (database) | Each seller enters their own `Api-Key` and `Secret-Key` in **Settings → Courier accounts**. Both are AES-256-GCM encrypted per account row with `CREDENTIAL_ENCRYPTION_KEY`, and nothing in the API can return one. |
| **Rollout** (runtime flag) | `steadfast_enabled`, off by default |

One global merchant key in the environment would mean every shop's parcels
booked on one merchant account — wrong commercially and wrong for liability.

### `STEADFAST_BASE_URL`

Default `https://portal.packzy.com/api/v1`, which is the URL Steadfast's V1
documentation prints. Overridable so a future sandbox needs no release — not
because one is known to exist; the document names none. Not secret.

### `STEADFAST_WEBHOOK_ENABLED`

**Must stay `false`.** `STEADFAST_WEBHOOK_CONTRACT_REQUIRED`: the supplied V1
documentation has no webhook section at all — no signature header, no HMAC
algorithm, no event id, no retry contract. A receiver with nothing to verify
would accept any POST, which means anyone could mark any parcel delivered and
move a seller's money. Startup refuses `true` in staging and production.

Polling is the complete V1 synchronisation path and is built to be sufficient
on its own. A webhook would make status arrive in seconds instead of minutes.

### Operator smoke test

For a one-off live check, the smoke tool reads two environment variables from
the **shell** — exported for that command only, never written into any file:

```bash
export STEADFAST_SMOKE_API_KEY='...'
export STEADFAST_SMOKE_SECRET_KEY='...'
python -m app.provider_smoke steadfast --env-credentials
```

Or, better, against a stored account so no key touches the shell at all:

```bash
python -m app.provider_smoke steadfast --account-id <uuid>
```

Read-only by default. `--probe-undocumented` records the real field names of
the four endpoints whose schema the documentation does not give;
`--allow-create` books one real parcel. The full checklist is in
`docs/providers/steadfast/IMPLEMENTATION.md`.

**How to verify.** The smoke tool passes end to end, the "Live verification"
table in `IMPLEMENTATION.md` is filled in with dates, and every `UNVERIFIED`
field name in the capability manifest is replaced with the observed one. Then
enable `steadfast_enabled` for one shop before enabling it globally.

---

## 10. Cloudflare R2

Private R2 stores original imports/payout files and generated exports. FastAPI
retains tenant/token checks. The existing worker deletes expired exports; account
deletion removes personal import/export objects while financial evidence follows
existing retention. Historical inline records remain readable. No file uses local disk.

All four application variables are now supplied, verified with temporary
PUT/GET/DELETE, and stored in the Northflank production secret group.

**Where to obtain**

1. Cloudflare dashboard → **R2 Object Storage** → **Create bucket**.
   Name it `ecomsbd-production`. Choose a location hint near your API.
2. **Do not enable public access.** Nothing here is meant to be world-readable:
   exports are served through the API against a single-use, expiring token, and
   payout statements are a seller's financial records.
3. **R2 → Manage API tokens → Create API token.**
   - Permission: **Object Read & Write**.
   - Scope: **this bucket only** — not "all buckets". Least privilege matters
     because these credentials sit in an environment variable on a web server.
   - The secret is **shown once**. Copy it now.
4. The token page also shows the **S3-compatible endpoint**, which embeds your
   account id.

| Variable | Format | Secret |
|---|---|---|
| `R2_ENDPOINT_URL` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` | No |
| `R2_BUCKET` | `ecomsbd-production` | No |
| `R2_ACCESS_KEY_ID` | 32 hex characters | **Yes** |
| `R2_SECRET_ACCESS_KEY` | 64 hex characters | **Yes** |

There is no separate `R2_ACCOUNT_ID` variable — the account id is the
subdomain of the endpoint. There is no `R2_PUBLIC_BASE_URL` either, because
nothing is served from a public bucket URL: `EXPORT_DOWNLOAD_TTL_SECONDS`
governs a token the API itself checks.

**Verification completed.** A temporary private object passed PUT/GET/DELETE.
Exports use their existing storage-key column; historical inline exports remain readable.

---

## 11. Firebase / FCM

Two different artifacts, and confusing them is the usual mistake.

### Mobile client config — a file, not a variable

`google-services.json` is read by the Firebase Gradle plugin at build time.

| | |
|---|---|
| **Where to obtain** | Firebase Console → Project settings → Your apps → **Add app → Android** → enter the package name → **Download google-services.json** |
| **Where it goes** | `apps/mobile_flutter/android/app/google-services.json` |
| **Secret?** | It carries identifiers and an API key that ships inside every APK, so it is not a high-value secret — but it is gitignored, because it is per-project configuration that should not be copied between environments by accident. |
| **Android package** | Final: `com.smply.app`. Firebase setup remains a separate external step; release certificate fingerprints require the owner's key. |

An iOS build additionally needs `GoogleService-Info.plist` in the Xcode project;
also gitignored.

### Backend sending — environment variables

| Variable | Purpose | Secret |
|---|---|---|
| `PUSH_TRANSPORT` | `disabled` or `fcm`. `mock` is refused outside local/test. | No |
| `FCM_PROJECT_ID` | Firebase project id | No |
| `FCM_CREDENTIALS_JSON` | Service-account JSON, as a single-line string | **Yes** |

**Where to obtain `FCM_CREDENTIALS_JSON`.** Firebase Console → Project settings
→ **Service accounts** → **Generate new private key**. That downloads a JSON
file. Paste its **contents** as one line — the code expects JSON content, not a
path. `service-account*.json` and `firebase-admin*.json` are gitignored; never
commit the file.

**Push is a second copy, never the only one** (master spec section 94).
Everything a push carries is already in the notification centre, which is a
screen the seller can open. With `PUSH_TRANSPORT=disabled` every attempt is
recorded as `NOT_CONFIGURED` — nothing is lost and nothing is faked. The
transport reports itself unconfigured rather than returning success, so the
delivery metrics stay honest.

**Also required in the app** when push is enabled: the `POST_NOTIFICATIONS`
permission in `AndroidManifest.xml`. It is now declared and requested by Firebase Messaging. Android Firebase
configuration matches `com.smply.app`.

**Verification completed.** FCM parsed the supplied service account and obtained
its scoped Google OAuth token. No notification was sent. Full credential JSON
is stored in `ecomsbd-production`; the operator's original remains ignored and
untracked. Device delivery is checked during the separate Android installation task.

---

## 12. Sentry

Optional, and strongly recommended: without it a crash in a seller's hands is
invisible. Its absence never stops the boot.

| Variable | Purpose | Secret |
|---|---|---|
| `SENTRY_DSN` | Where events go. Empty disables reporting. | Treat as secret on the backend |
| `SENTRY_ENVIRONMENT` | Tag on every event. Defaults to `APP_ENV`. | No |
| `SENTRY_RELEASE` | Which build an event came from. Defaults to `ecomsbd-backend@<version>`. | No |
| `SENTRY_TRACES_SAMPLE_RATE` | Performance tracing, `0.0`–`1.0`. Traces are billed. | No |

**Where to obtain.** [sentry.io](https://sentry.io) → create an organisation →
**create two projects**: one Python/FastAPI for the backend, one Flutter for the
app. Each has its own DSN under Project Settings → Client Keys (DSN). Do not
share one DSN between them — you lose the ability to tell a server error from a
client crash at a glance.

**Environment and release tagging.** Set `SENTRY_ENVIRONMENT=production` so
staging noise does not page you, and set `SENTRY_RELEASE` per deploy (the
commit sha is the easiest source) so a regression can be attributed to the
change that caused it.

### What is already guaranteed not to reach Sentry

Wired in `app/core/observability.py`, so this needs no configuration:

- `send_default_pii=False` — Sentry does not attach user identifiers or request
  bodies by default.
- A `before_send` scrubber runs the same redaction as the logs over `request`,
  `extra`, `contexts` and `breadcrumbs`, replacing **customer phone numbers**
  (masked to `01712****78`), **API keys and tokens**, **courier credentials**,
  and **`Authorization` / `Api-Key` / `Secret-Key` headers**.

**How to verify.** Trigger a deliberate error and confirm it appears in Sentry
**with the phone number masked** and no `Authorization` header attached.

### Flutter Sentry

`ECOMSBD_SENTRY_DSN` exists as a `--dart-define` and is **read but not yet
used**: `sentry_flutter` is not a dependency. Wiring the mobile SDK is part of
`SENTRY_CONFIGURATION_REQUIRED`. A mobile DSN is a public client key by design —
it ships in the app either way — but keep the *backend* DSN out of the client.

---

## 13. Release identifiers — not environment variables

These are decisions, not secrets, and each belongs somewhere other than `.env`.

### Android package id — resolved

The owner permanently selected **`com.smply.app`**. Gradle `applicationId`,
namespace and the `MainActivity` package/path now match this decision.

| | |
|---|---|
| **Where it belongs** | `apps/mobile_flutter/android/app/build.gradle.kts` — both `namespace` and `defaultConfig.applicationId` — plus the `MainActivity` package path |
| **Why it is urgent** | Play rejects anything under `com.example`. The id is **permanent once published**: changing it means shipping a different app and losing every install, review and subscription. |
| **Dependent configuration** | Future Google Android/Firebase/Play registrations use `com.smply.app`; the production `PLAY_PACKAGE_NAME` example already matches. No external provider has been configured by this change. |

Backend placeholder-rejection logic/tests and Apple identifiers are unchanged.

### Android release signing — `RELEASE_SIGNING_REQUIRED`

Release tasks use only the `release` signing config and fail if private settings
or the keystore are missing, or the standard debug key is selected. Debug builds
are unchanged. Supply the owner's ecomsbd release/upload keystore outside the
repository and use ignored `android/key.properties` or `ECOMSBD_RELEASE_*`
environment secrets; no passwords or aliases are hardcoded in Gradle.

Follow [the exact key generation, local/CI configuration and SHA-1/SHA-256 commands](../apps/mobile_flutter/android/RELEASE_SIGNING.md).
`key.properties`, `*.jks` and `*.keystore` are ignored globally. No key or password
has been generated. Minification/resource-shrinking settings remain unchanged.

### Apple bundle identifier

The App ID in the Apple Developer portal and the Xcode bundle identifier must
match, and Supabase's Apple Client IDs must include the identifier the token is minted
for. Conventionally the same reverse-domain string as the Android id.

### Version and build number

`apps/mobile_flutter/pubspec.yaml` → `version: 0.1.0+1`. The part after `+` is
the build number; Play orders releases by it and rejects a repeat.

### The one place the package id crosses into `.env`

`PLAY_PACKAGE_NAME` on the backend must equal the Gradle `applicationId`
exactly. It is duplicated there because Play purchase verification is bound to
the package name and the server cannot read Gradle.

---

## 14. Google Play Billing

`PLAY_SERVICE_ACCOUNT_REQUIRED`. Master spec section 90 forbids granting a paid
entitlement from a client callback: without the Play Developer API there is no
way to ask Google whether a purchase token is real, so no purchase can be
honoured.

**Everything except the HTTP call already exists** — replay protection, tenant
binding, package and product validation, plan mapping, acknowledgement
bookkeeping, the state machine, RTDN parsing and deduplication, all tested
against fixtures. The remaining work is one `PlayApiClient`.

**Keep `play_billing_enabled` (the runtime flag) off** until all four hold:

1. the final package id is chosen and published;
2. the app exists in Play Console;
3. a service account has Play Developer API access;
4. subscription products exist and are mapped.

| Variable | Where to obtain | Format | Secret |
|---|---|---|---|
| `PLAY_PACKAGE_NAME` | your Gradle `applicationId` | `com.yourorg.ecomsbd` | No |
| `PLAY_SERVICE_ACCOUNT_JSON` | see below | JSON on one line | **Yes** |
| `PLAY_PRODUCT_PLAN_MAP` | your Play Console products | `{"ecomsbd_pro_monthly":"pro"}` | No |
| `PLAY_RTDN_SHARED_SECRET` | you choose it | random string | **Yes** |

**Service account, step by step**

1. Google Cloud Console → the project linked to Play → **IAM & Admin → Service
   accounts → Create service account**.
2. **Keys → Add key → JSON.** Downloads once; paste the contents as one line.
3. Enable the **Google Play Android Developer API** in that project.
4. Play Console → **Users and permissions → Invite new user** → the service
   account's email → grant **View financial data** and **Manage orders and
   subscriptions**, scoped to this app.
5. Permissions can take up to 24 hours to propagate. A 401 immediately after
   granting them is usually just that.

**Products.** Play Console → Monetize → Subscriptions. Create each plan, then
map its product id to an ecomsbd plan code in `PLAY_PRODUCT_PLAN_MAP`. The
check refuses a configuration where the package name is still `com.example.*`.

**`DISTRIBUTION_CHANNEL`** decides whether an out-of-app payment CTA may be
shown at all (master spec section 27.1). Set it to `PLAY` for a build shipped
through Play. Getting this wrong is a policy violation, not a cosmetic bug.

**Before release: re-check Play's billing and alternative-billing policy.** The
conclusion that Bangladesh is not in the alternative-billing country list was
verified against the sources the specification used, and policy changes.

**How to verify.** Set the four values, wire a `PlayApiClient`, enable the flag;
`GET /v1/billing/channel` reports `play` as `available`, and a sandbox purchase
appears in `GET /v1/billing/history` as `VERIFIED`.

---

## 15. bKash

`BKASH_MERCHANT_SETUP_REQUIRED`. **Leave every bKash line blank.** The runtime
flag `bkash_web_billing_enabled` is off by default, and nothing needs turning
off explicitly.

The implementation is transport-stubbed: the checkout record, our own reference
minted before anyone is redirected, callback verification, the recurring/
one-time split, dunning and cancellation all exist. A `BkashApiClient` is the
remaining work.

The six variables exist because the provider code reads them, and **only those
six**. No endpoint path is hard-coded, because which products a merchant can
use — gateway, tokenized checkout, subscription mandate — depends on their
contract, and master spec section 91 forbids inventing one.

| Variable | Secret | Notes |
|---|---|---|
| `BKASH_BASE_URL` | No | From *your* merchant documentation, not from a blog post |
| `BKASH_APP_KEY` | **Yes** | |
| `BKASH_APP_SECRET` | **Yes** | |
| `BKASH_USERNAME` | **Yes** | |
| `BKASH_PASSWORD` | **Yes** | |
| `BKASH_WEBHOOK_SECRET` | **Yes** | Only if your contract defines a callback signature |

**All five or none** — the first five are refused if partly filled.

**Where to obtain.** bKash merchant onboarding. You need the credentials *and*
the API documentation for the products your merchant account is granted *and*
the callback signature scheme. Do not proceed on a sandbox document that
describes a product you were not sold.

---

## 16. Admin / ops

Platform admin is a **separate identity** from any seller account. The two do
not overlap: a stolen app session is worth nothing here, and a stolen admin
token cannot post an order.

With nothing configured there is **no admin access at all**. That is the
correct posture, not an omission — an ops console reachable before anyone has
provisioned access is a vulnerability.

### Two sources, in order

1. **A `platform_admins` row**, matched by keyed hash. The normal path: tokens
   are issued individually, carry a role, and can be revoked. Every audit entry
   names the operator.
2. **`ADMIN_API_TOKENS`** — a bootstrap credential for the first operator on a
   fresh deployment, before any row exists. It maps to a `SUPERADMIN` labelled
   `bootstrap:<n>` so audit entries say plainly that the shared credential was
   used.

### `ADMIN_API_TOKENS`

| | |
|---|---|
| **Purpose** | Bootstrap admin access |
| **Required** | No — and leave it blank in steady state |
| **Secret** | **Yes** |
| **Format** | Comma-separated, or a JSON array. Each at least 32 characters. |
| **Generate** | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |

Startup **refuses** any deployed configuration whose bootstrap tokens are
shorter than 32 characters or carry an `INSECURE_DEV_` placeholder. There is no
default production admin credential and none is generated for you.

**Recommended sequence**

1. Set one strong bootstrap token; deploy.
2. Use it to create real `platform_admins` rows, one per operator.
3. Clear `ADMIN_API_TOKENS` and redeploy.

A bootstrap token cannot be revoked without a redeploy, and everything it does
audits only as `bootstrap:0`.

### `ADMIN_REVEAL_TTL_SECONDS`

How long a support "reveal customer PII" grant lasts before it must be asked
for again. 300 seconds by default. Every reveal is audited.

### Repair tools and support access

The repair actions and support bundle live under `/v1/admin`, authenticated by
`X-Admin-Token` and excluded from the public schema. They need no configuration
of their own beyond an admin identity — the role on the `platform_admins` row
decides what each operator can do.

---

## 17. CORS, trusted hosts and URLs

| Variable | Purpose | Secret |
|---|---|---|
| `CORS_ALLOW_ORIGINS` | Browser origins allowed to call this API | No |
| `TRUSTED_HOSTS` | `Host` headers this deployment answers to | No |
| `MINIMUM_SUPPORTED_APP_VERSION` | Clients below it get a stable upgrade error | No |

Both lists accept a comma-separated string or a JSON array. Blank means empty.

**The mobile app is not a browser and needs no CORS entry.** Leave
`CORS_ALLOW_ORIGINS` blank unless a browser client actually exists.

**Refused at startup in staging and production**

- `*` — the API is served with `allow_credentials=True`, so a wildcard origin
  would let any site make authenticated requests on a seller's behalf;
- any `localhost` / `127.0.0.1` / `10.0.2.2` origin — those belong in local
  configuration;
- in production, any plain-`http` origin;
- `*` in `TRUSTED_HOSTS`.

**`TRUSTED_HOSTS` empty** is correct only behind a proxy that already enforces
the host. If the app is reachable directly, set it. The check reports an empty
list as `MISSING` rather than refusing, because it cannot tell which of the two
is true from inside the process.

**How to verify**

```bash
curl -H "Origin: https://evil.example.com" -I https://api.yourdomain.com/health/live
# no access-control-allow-origin header in the response

curl -H "Host: evil.example.com" -I https://api.yourdomain.com/health/live
# 400 when TRUSTED_HOSTS is set
```

---

## 18. Backups and the restore drill

`STAGING_RESTORE_DRILL_REQUIRED`. Master spec section 48: *do not claim backup
safety unless restore has been tested.*

- **Destination.** Supabase's automated backups on a paid plan, plus
  `infra/backup/backup.sh` to a destination you control. Two independent copies,
  because "the provider has it" is not a backup strategy you can test.
- **Verification.** `infra/backup/verify_restore.py` checks the migration head,
  every mapped table, the per-tenant receivable/ledger invariant, the
  oversettlement constraint, and that no customer phone is stored in clear text.
- **What has been done.** A real drill on PostgreSQL 16.13, recorded in
  `docs/runbooks/DATABASE_RESTORE.md`, and CI runs a dump/restore/verify cycle
  on every build.
- **What is still missing.** That drill used test data. Restore *time* on a
  production-shaped database is unmeasured, so the stated RTO is an estimate.
  Run `infra/backup/restore.sh` against staging and record the duration.

---

## 19. Final check

```bash
cd backend
python -m app.check_production_config --env-file ../.env.production.local --env production
```

A healthy report for a first deploy, with the integrations still to come:

```
DATABASE ................ OK
REDIS ................... OK
CRYPTO .................. OK
SIGN-IN ................. OK
EMAIL ................... MISSING
GOOGLE AUTH ............. DISABLED
APPLE AUTH .............. DISABLED
PHONE OTP ............... OK
STEADFAST ............... OK
R2 ...................... MISSING
FCM ..................... DISABLED
SENTRY .................. OPTIONAL/MISSING
PLAY BILLING ............ DISABLED
BKASH ................... DISABLED
ADMIN ................... OK
URLS / CORS ............. OK
```

Each line carries a one-sentence explanation underneath it, which the excerpt
above leaves out. `STEADFAST ... OK` reads *READY FOR MERCHANT ACCOUNT* — there
is nothing in this file to fill in for it.

Exit status is about **boot safety, not completeness**. Non-zero means this
configuration is unsafe or would not start. `MISSING`, `DISABLED` and `PARTIAL`
all exit zero: the system runs without them and says so.

Then:

```bash
cd backend && alembic upgrade head
curl https://api.yourdomain.com/health/ready
```

`/health/ready` returns 503 while the database or Redis is unreachable, or
while no migration has been applied — so a failed deploy stops at the load
balancer rather than at a seller.
