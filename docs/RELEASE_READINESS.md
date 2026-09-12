# Release readiness

**Last updated 2026-09-12, after Flutter seller authentication integration.**

This is the gate. Nothing below is a "nice to have": each row is something that
must be true before ecomsbd charges a real seller or handles a real parcel.

A blocker is listed with **what is missing, why it is required, where it is
configured, and how to check it is done** — because a checklist that only says
"needs credentials" gets ticked by someone who found *a* credential.

Every environment variable named below is defined in `.env.production.example`
and explained, with where to obtain it, in **`docs/PRODUCTION_ENV_SETUP.md`**.
`python -m app.check_production_config` reports which of them are set without
printing a value.

---

## Summary

| | Count |
|---|---:|
| **Blockers — must be resolved before any public release** | 13 |
| Blockers that are an operator decision (no engineering work) | 2 — §2, §10 |
| Blockers that need a third-party account | 9 — §3, §4, §5, §6a, §6c (×2), §7, §8, §9 |
| Blockers waiting on provider documentation | 1 — §5a |
| **Auth platform release follow-up** | **1 — §6 Apple platform provisioning/device validation; Flutter integration implemented** |
| Deferred, no longer a blocker | §6b `SMS_PROVIDER_REQUIRED` |

**ecomsbd cannot be released today.** The backend production sign-in model is
implemented: **email/password + Google + Apple**, sharing existing sessions and
shop onboarding. Phone OTP is deferred and Facebook/Meta login is excluded.
Flutter now implements the email flows and native provider-token exchange using
the existing session repository; OTP UI is disabled by default. Provider setup
and device sign-in checks remain external. Apple is unavailable on the current
Android runner; an iOS release needs its runner and Apple provisioning.

The Android package ID is now fixed as `com.ecomsbd.app`. Remaining operator
configuration includes the signing key, courier account, billing/email providers,
push, object storage and error tracking. Each is refused honestly today
rather than faked.

**What changed on 2026-09-12**

- `SMS_PROVIDER_REQUIRED` is **no longer a release blocker**. Phone OTP is
  deferred, so an SMS gateway is not on the path to first release. It survives
  below as a *deferred* item for future use.
- Backend `SELLER_AUTH_IMPLEMENTATION_REQUIRED` and Flutter login integration
  are implemented. Provider credentials and platform/device checks remain.
- `TRANSACTIONAL_EMAIL_PROVIDER_REQUIRED` is **new**: email/password auth needs
  verification and password-reset mail. The Resend adapter is implemented;
  an account, verified sender and send-only key are still required.
- `PRODUCTION_DATABASE_CONFIGURATION_REQUIRED` and
  `REDIS_CONFIGURATION_REQUIRED` are recorded explicitly. They were always
  true; they were never written down.

---

## 1. `PACKAGE_ID_DECISION_REQUIRED` — resolved

| | |
|---|---|
| **Status** | **RESOLVED — permanent owner decision: `com.ecomsbd.app`.** |
| **Implemented** | Gradle `applicationId` and namespace plus the `MainActivity` package/path all use `com.ecomsbd.app`. The production example's `PLAY_PACKAGE_NAME` matches; no provider account has been configured. |
| **Why it is required** | Play rejects any id under `com.example`. More importantly the id is **permanent once published**: changing it later means shipping a different app and losing every install, review and subscription. Play Billing verification is bound to the package name (section 90), so it must be settled *before* billing is configured, not after. |
| **Where it is configured** | `apps/mobile_flutter/android/app/build.gradle.kts` — `namespace` and `defaultConfig.applicationId`; the `MainActivity` package path; `PLAY_PACKAGE_NAME` in the backend environment. |
| **Validation** | No scaffold package references remain in Android source/config. Future Play/Google/Firebase registrations must use the final ID; live backend provider health still depends on its deployed environment. |
| **Note** | Backend rejection of placeholder package IDs and its negative tests are retained. Apple identifiers are unchanged. |

## 2. `RELEASE_SIGNING_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — operator decision** |
| **What is missing** | The owner's ecomsbd release/upload keystore and private signing values. Release signing is implemented and refuses missing configuration or the standard debug key; normal debug builds need no release secrets. |
| **Why it is required** | Play requires a properly signed upload. Protect and back up the private key; Play App Signing distinguishes the upload key from the app signing key. |
| **Where it is configured** | Ignored `apps/mobile_flutter/android/key.properties` or `ECOMSBD_RELEASE_*` environment values. See [release signing and both SHA fingerprints](../apps/mobile_flutter/android/RELEASE_SIGNING.md). |
| **Validation** | `flutter build appbundle --release` produces an artifact whose signer is not `androiddebugkey`. |
| **Also unblocks** | Minification and resource shrinking, both off today because obfuscated stack traces with no mapping upload are unreadable in production. |

## 3. `PLAY_SERVICE_ACCOUNT_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — needs a Google Play Console account** |
| **What is missing** | A service account with Play Developer API access, the subscription products created in the console, and Real-time Developer Notifications configured. |
| **Why it is required** | Section 90 forbids granting a paid entitlement from a client callback. Without the API there is no way to ask Google whether a purchase token is real, so **no Play purchase can be honoured**. |
| **Where it is configured** | `PLAY_PACKAGE_NAME`, `PLAY_SERVICE_ACCOUNT_JSON`, `PLAY_PRODUCT_PLAN_MAP`, `PLAY_RTDN_SHARED_SECRET`; flag `play_billing_enabled`. |
| **What exists already** | Everything except the HTTP call. Replay protection, tenant binding, package and product validation, plan mapping, acknowledgement bookkeeping, the state machine, RTDN parsing and deduplication are implemented and tested against fixtures. The remaining work is one `PlayApiClient` implementation. |
| **Validation** | Set the four variables, wire a `PlayApiClient`, enable the flag; `GET /v1/billing/channel` reports `play` as `available`; a sandbox purchase verifies and appears in `GET /v1/billing/history` as `VERIFIED`. |
| **Before release** | **Re-check Play's billing and alternative-billing policy.** Section 27.1's conclusion — that Bangladesh is not in the alternative-billing country list — was verified against the sources this specification used, and policy changes. |

## 4. `BKASH_MERCHANT_SETUP_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — needs bKash merchant onboarding** |
| **What is missing** | Merchant credentials, the API documentation for the products this merchant is granted, and the callback signature scheme. |
| **Why it is required** | Section 91 forbids hard-coding undocumented endpoint paths. Which products a merchant can use — gateway, tokenized checkout, subscription mandate — depends on their contract, and the one-time-payment fallback exists because a mandate cannot be assumed. |
| **Where it is configured** | `BKASH_BASE_URL`, `BKASH_APP_KEY`, `BKASH_APP_SECRET`, `BKASH_USERNAME`, `BKASH_PASSWORD`, `BKASH_WEBHOOK_SECRET`; flag `bkash_web_billing_enabled`. |
| **What exists already** | The checkout record, our own reference minted before anyone is redirected, callback verification, the recurring/one-time split, dunning and cancellation. A `BkashApiClient` implementation is the remaining work. |
| **Validation** | Sandbox payment completes; `POST /v1/billing/web/confirm` returns `granted: true`; a replayed callback changes nothing. |

## 5. `STEADFAST_LIVE_CREDENTIAL_TEST_REQUIRED`

| | |
|---|---|
| **Status** | **Code complete; needs a merchant account to verify.** The documentation blocker is resolved — Steadfast's V1 API documentation was supplied on 2026-09-11, read in full, and implemented against. |
| **What is missing** | A merchant `Api-Key` and `Secret-Key`. Nothing else. |
| **Why it is required** | Every code path is tested against contract fixtures over a fake transport, which proves the parsing, the state machine and the safety rules. It does not prove that a real key authenticates, that a real create returns what the document says, or what the four undocumented-schema endpoints actually return. |
| **Where it is configured** | Seller-side: Settings → Courier accounts. Operator-side: `STEADFAST_SMOKE_API_KEY` / `STEADFAST_SMOKE_SECRET_KEY` for the smoke tool. Flag `steadfast_enabled` gates rollout and is **off** by default. |
| **How to verify** | `python -m app.provider_smoke steadfast --account-id <uuid>` (read-only), then `--probe-undocumented` to record the real field names, then `--allow-create` with a recipient fixture for one real parcel. The checklist is in `docs/providers/steadfast/IMPLEMENTATION.md`. |
| **Validation** | The smoke tool passes end to end; the "Live verification" table in `IMPLEMENTATION.md` is filled in with dates; any `UNVERIFIED` field name in the manifest is replaced with the observed one. |
| **Consequence today** | Manual courier mode remains the working path and is unconditional. The money core, profit engine and alerts run on it unchanged. |

## 5a. `STEADFAST_WEBHOOK_CONTRACT_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — the supplied documentation has no webhook section at all.** |
| **What is missing** | A signature header name, an HMAC algorithm, a payload shape, an event id and a retry contract. Not one of those appears in Steadfast's V1 documentation. |
| **Why it is required** | It is not, for correctness — polling is the complete V1 synchronisation path and is built to be sufficient on its own. A webhook would make status arrive in seconds instead of minutes. |
| **What exists already** | The receiving route, raw-body persistence, replay protection by body hash, the delivery state machine, verifier and parser ports, a constant-time signature comparator, and the queue hand-off. All tested. |
| **Why it stays off** | `SteadfastWebhookVerifier.is_configured` returns `False` unconditionally. A verifier that returned `True` because there is nothing to check would not be a disabled webhook — it would be an open endpoint letting anyone mark any parcel delivered and move a seller's money. `Settings` refuses to boot a deployed environment with `STEADFAST_WEBHOOK_ENABLED=true`. |
| **Validation** | Steadfast supplies a webhook contract; the verifier implements it; the capability moves from `unknown` to `true` with a date. |

## 6. Seller authentication — backend and Flutter integration implemented

| | |
|---|---|
| **Status** | **Backend and Flutter integration implemented. External provider setup and device validation remain; Apple needs an iOS runner/provisioning for native release.** |
| **Implemented** | Register/login, Argon2id passwords, email verification/resend, forgot/reset/change password, Google and Apple identity-token verification, shared refresh rotation, session revocation, audit events and shop onboarding. No second session architecture. |
| **Endpoints** | `POST /v1/auth/register`, `/login`, `/email/verify`, `/email/resend`, `/password/forgot`, `/password/reset`, `/password/change`, `/oauth/google`, `/oauth/apple`. Link landing pages are served under `/auth` on `PUBLIC_BASE_URL`. |
| **Identity safety** | Provider subject remains authoritative. Linking requires a unique existing user and verified email proof on both sides; unverified claims, Apple relay addresses, Google third-party mailboxes and ambiguous users do not automatically merge. |
| **Production flags** | `EMAIL_PASSWORD_AUTH_ENABLED=true`, `GOOGLE_AUTH_ENABLED=true`, `APPLE_AUTH_ENABLED=true`, `PHONE_OTP_LOGIN_ENABLED=false`. No SMS gateway required. |
| **External configuration** | Google client IDs, `APPLE_CLIENT_ID`, and Resend sender/API configuration. Apple's private key, Team ID and Key ID are optional for the implemented identity-token flow, which uses public JWKS. |
| **Migration** | `c41f8b2ad7e5` retained; creates identities/link tokens and permits users without phone numbers. Auth fixtures apply it on SQLite; PostgreSQL SQL rendering checked without a live database. |
| **Validation** | Targeted auth/config tests cover forged/wrong-audience/expired tokens, linking, reset replay, revocation and all three methods creating shops with the existing session system. Real provider sign-in and email delivery still require credentials. |
| **Mobile integration** | Email login/register/verification/resend/forgot/reset screens; native Google/Apple ID tokens verified by the existing backend; nullable phone profiles; shared logout/refresh; existing shop → Home, no shop → Create Shop, multiple shops → existing tenant selection. OTP UI and routes disabled by default. |
| **Mobile release follow-up** | Supply Google web client ID, register Android package/signing fingerprints and validate real sign-in. Apple safely reports unavailable on Android; provisioning an iOS runner with Sign in with Apple capability is required to release the native Apple flow. Existing browser email verification/reset links work without app-link registration. See [Flutter auth configuration](../apps/mobile_flutter/env/README.md). |

## 6a. `TRANSACTIONAL_EMAIL_PROVIDER_REQUIRED`

| | |
|---|---|
| **Status** | **Code complete; needs an account and sender configuration** |
| **What is missing** | A Resend account, send-only API key, and verified sending domain. |
| **Why it is required** | Email verification and forgot-password links need deliverable mail. |
| **Where it is configured** | `EMAIL_TRANSPORT`, `EMAIL_FROM_ADDRESS`, `EMAIL_FROM_NAME`, `EMAIL_API_BASE_URL`, `EMAIL_API_KEY`. |
| **What exists already** | Resend REST adapter with payload validation, idempotency headers, delivery outcomes and redacted errors; browser verification/reset pages; existing `disabled` / `console` / `mock` / `provider_api` interfaces. Contract: [Resend send email](https://resend.com/docs/api-reference/emails/send-email). |
| **Safety already enforced** | `EMAIL_TRANSPORT=console` writes the message to the log and is **refused at startup in staging and production**: a reset link is a credential, so a transport that prints it publishes account takeovers to everyone who can read the log. `mock` is refused for the same reason. `EMAIL_PASSWORD_AUTH_ENABLED=true` with no deliverable transport is refused. |
| **Validation** | A verification email arrives at a real inbox from the production sender, and the delivery record says `SENT` rather than `NOT_CONFIGURED`. |

## 6b. `SMS_PROVIDER_REQUIRED` — **deferred, no longer a release blocker**

| | |
|---|---|
| **Status** | **DEFERRED.** Phone OTP sign-in is off in production (`PHONE_OTP_LOGIN_ENABLED=false`), so no SMS gateway is on the path to first release. |
| **Why it is still listed** | OTP code is retained for possible future use. It is not required by any production login method. |
| **What is missing** | A Bangladeshi SMS gateway, a registered sender id, and that gateway's segment and cost rules. |
| **Where it is configured** | `PHONE_OTP_LOGIN_ENABLED=true`, `OTP_PROVIDER=sms_gateway`, `OTP_PROVIDER_SECRET`, `SMS_TRANSPORT=sms_gateway`. |
| **What exists already** | The provider-neutral transport, payload validation, GSM-03.38/UCS-2 segment estimation, per-segment quota metering, Banglish templates, and delivery records. Section 22's rule holds: the provider's own segment count wins over our estimate, and both are stored. |
| **What changed** | The dev OTP provider guard and the `OTP_PROVIDER_SECRET` requirement are now conditional on `PHONE_OTP_LOGIN_ENABLED`. A deployment with OTP off is no longer asked for an SMS gateway key it will never use — requiring credentials for a disabled integration is how operators learn to fill in values blindly. With OTP **on**, all three production guards apply unchanged. |
| **Not affected** | SMS *alerts* are a separate concern from OTP sign-in and remain `SMS_TRANSPORT=disabled`. |
| **Validation** | With `PHONE_OTP_LOGIN_ENABLED=true`: a real OTP arrives and `notification_deliveries` records the provider's segment count. |

## 6c. `PRODUCTION_DATABASE_CONFIGURATION_REQUIRED` / `REDIS_CONFIGURATION_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — needs a Supabase project and a managed Redis** |
| **What is missing** | A production PostgreSQL connection string and a production Redis URL. |
| **Why they are required** | Nothing runs without the database. Without Redis the API refuses to start in a deployed environment, and the worker refuses outright — a worker that cannot see the queue would report itself healthy while courier polling, payment sync, outbox dispatch, alerts and the weekly summary all silently stopped. |
| **Where they are configured** | `DATABASE_URL`, `REDIS_URL`, plus the `DATABASE_POOL_*` settings. Supabase pooler caveats — which port, why the scheme is rewritten to `postgresql+asyncpg`, why `sslmode` is stripped — are in `docs/PRODUCTION_ENV_SETUP.md` sections 2 and 3. |
| **Architecture note** | Supabase is managed PostgreSQL and nothing else. No Supabase Auth, no PostgREST, no Supabase client in Flutter, and **no service-role key anywhere** — the app reaches its data only through the ecomsbd backend, where the tenancy guards are. |
| **Validation** | `alembic upgrade head` applies cleanly and `/health/ready` reports both `database` and `redis` as `ok`. |

## 7. `FCM_CREDENTIALS_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — needs a Firebase project** |
| **What is missing** | A Firebase project and service credentials. |
| **Why it is required** | Push is how a seller learns money did not arrive without opening the app. |
| **Severity** | **Lower than the rest.** Section 94 makes push a second copy: everything is already in the notification centre, and the product works without it. |
| **Where it is configured** | `FCM_PROJECT_ID`, `FCM_CREDENTIALS_JSON`, `PUSH_TRANSPORT=fcm`; `google-services.json` in the app; `POST_NOTIFICATIONS` in the manifest. |
| **Validation** | A push arrives on a device and opens the exact order it names. |

## 8. `R2_CREDENTIALS_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — needs a Cloudflare R2 bucket** |
| **What is missing** | A bucket and access keys. |
| **Why it is required** | Payout source files and generated exports currently live in database rows. That works and is correct, and it makes the database grow with every statement a seller uploads. |
| **Where it is configured** | `R2_ENDPOINT_URL`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. |
| **Validation** | A statement import writes a `storage_key` and the row's inline content is null; the object is retrievable. |
| **Note** | Moving the content is a migration, not a redesign: `storage_key` already exists on both tables. |

## 9. `SENTRY_CONFIGURATION_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — needs a Sentry project** |
| **What is missing** | A DSN for the backend and one for the mobile app. |
| **Why it is required** | Without it a crash in a seller's hands is invisible. Section 49 requires error tracking on both. |
| **Where it is configured** | `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE` on the backend; `ECOMSBD_SENTRY_DSN` as a `--dart-define` on the app. |
| **Mobile caveat** | `sentry_flutter` is **not a dependency yet**, so `ECOMSBD_SENTRY_DSN` is compiled in and unused. Wiring the mobile SDK is part of this blocker, not just supplying a DSN. |
| **Validation** | A deliberate test error appears in Sentry **with the phone number redacted** — the `before_send` hook and `send_default_pii=False` are already wired. |

---

## 10. `STAGING_RESTORE_DRILL_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — needs a staging environment** |
| **What is missing** | A restore drill against production-shaped data. |
| **Why it is required** | Section 48: *"do not claim backup safety unless restore has been tested."* |
| **What has been done** | A real drill on PostgreSQL 16.13: full schema dumped with `pg_dump --format=custom`, restored into a separate database with `pg_restore`, and verified by `infra/backup/verify_restore.py` — migration head, every mapped table, the per-tenant receivable/ledger invariant, the oversettlement constraint, and that no customer phone is stored in clear text. Recorded in `docs/runbooks/DATABASE_RESTORE.md`. |
| **What that does not prove** | The dataset was test data, not a production-shaped one, and there is no staging environment. Restore *time* on a real database is unmeasured, so the stated RTO is an estimate. |
| **Validation** | Run `infra/backup/restore.sh` against staging, record the duration and the verification output in the drill log. |

---

## Not blockers, but do them before the first paying seller

- **Decide the pricing.** ৳199/৳399 is section 26's hypothesis. Prices and
  entitlement values are configurable without a release
  (`PLAN_PRICE_OVERRIDES`, `PLAN_ENTITLEMENT_OVERRIDES`), so this is a product
  decision, not an engineering one.
- **Provision per-operator admin tokens** and leave `ADMIN_API_TOKENS` empty.
  A shared bootstrap token cannot be revoked without a redeploy, and every
  audit entry it writes says only `bootstrap:0`.
- **Set `DISTRIBUTION_CHANNEL`** to match how the build is actually shipped. It
  decides whether an external payment CTA may be shown at all (section 27.1).
- **Set `MINIMUM_SUPPORTED_APP_VERSION`** once there is more than one build in
  the wild.
- **Point `PUBLIC_BASE_URL` at https.** Production refuses to start otherwise,
  and also refuses a localhost host, so a development URL cannot survive a
  deploy by accident.
- **Set `TRUSTED_HOSTS`** unless the API sits behind a proxy that already
  enforces the Host header. The config check reports an empty list rather than
  refusing, because it cannot tell from inside the process which is true.
- **Set `SENTRY_RELEASE`** per deploy, ideally to the commit sha. Without it
  every regression looks like it has always been there.
- **Keep `CORS_ALLOW_ORIGINS` empty** until a browser client exists. The mobile
  app is not a browser and needs no entry; a wildcard, a localhost origin and
  (in production) a plain-http origin are all refused at startup.

---

## Release gates

Every one of these must pass. They are ordered so a failure stops the cheapest
thing first.

| Gate | How |
|---|---|
| Lint, format, types | `ruff check`, `ruff format --check`, `mypy` — all of `app tests migrations` |
| Backend suite on **PostgreSQL** | `TEST_DATABASE_URL=postgresql+asyncpg://... pytest -q`. A SQLite-only green run is **not** release evidence (ADR 0004). |
| Migration round trip | `alembic upgrade head && alembic downgrade base && alembic upgrade head` against PostgreSQL, with no drift |
| Flutter | `flutter analyze`, `flutter test`, `dart format --set-exit-if-changed` |
| Secret scan | `gitleaks` over the full history; CI also refuses a committed `backend/.env` |
| Dependency audit | `pip-audit` on the backend lock; `flutter pub outdated` reviewed |
| Restore drill | Passed within the last 30 days, logged in `DATABASE_RESTORE.md` |
| Production config | `cd backend && python -m app.check_production_config --env-file ../.env.production.local --env production` exits 0. It never prints a value, so it is safe to run on a shared screen. |
| Production startup | `APP_ENV=production` boots. It fails fast on a missing or placeholder secret, a reused one, the dev OTP provider while OTP login is on, non-https or localhost URLs, SQLite, a wildcard or localhost CORS origin, a half-configured R2 or bKash, a `console`/`mock` email transport, an enabled provider missing its credentials, and a deployment nobody can sign in to. |
| Committed secrets | `.env.production.example` holds placeholders only; `.env.production.local` is gitignored and untracked. |
| Blockers | Every row above resolved or explicitly accepted, in writing, by the operator |

---

## Where configuration lives

| File | Committed? | What it is |
|---|---|---|
| `.env.production.example` | yes | The authoritative production template. Every variable, with `# REQUIRED` / `# OPTIONAL` / `# REQUIRED WHEN ...` and the reason it exists. Placeholders only. |
| `.env.production.local` | **no — gitignored** | The operator's working copy. Same variable names, real values. |
| `docs/PRODUCTION_ENV_SETUP.md` | yes | Where to obtain every value, provider by provider, with the operator checklist at the top. |
| `.env.example` | yes | Development only. Local defaults boot the stack with nothing set. |
| `apps/mobile_flutter/env/production.example.json` | yes | Flutter `--dart-define-from-file` template. **Non-secret values only** — a dart-define is compiled into the APK and can be read out of it. |
| `backend/app/check_production_config.py` | yes | The check command. Reports status per area; never prints a value. |
