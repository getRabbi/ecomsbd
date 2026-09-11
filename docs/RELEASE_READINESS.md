# Release readiness

**Last updated 2026-09-11, end of Phase C.**

This is the gate. Nothing below is a "nice to have": each row is something that
must be true before ecomsbd charges a real seller or handles a real parcel.

A blocker is listed with **what is missing, why it is required, where it is
configured, and how to check it is done** — because a checklist that only says
"needs credentials" gets ticked by someone who found *a* credential.

---

## Summary

| | Count |
|---|---:|
| **Blockers — must be resolved before any public release** | 9 |
| Blockers that are an operator decision (no engineering work) | 3 |
| Blockers that need a third-party account | 6 |
| Engineering work outstanding | 0. Phase C is code complete as of 2026-09-11; what remains for Steadfast is live verification, not engineering. |

**ecomsbd cannot be released today.** Not because the software is incomplete —
Phases A, B, D, E and F are done and tested — but because it is not yet
connected to the outside world it needs: no package id, no signing key, no
courier, no billing provider, no SMS, no push, no object storage, no error
tracking.

Every one of those is configuration an operator supplies. None of them needs a
line of code to be rewritten, and each is refused honestly today rather than
faked.

---

## 1. `PACKAGE_ID_DECISION_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — operator decision, no engineering work** |
| **What is missing** | The Android `applicationId`. It is still the Flutter scaffold default `com.example.ecomsbd`. |
| **Why it is required** | Play rejects any id under `com.example`. More importantly the id is **permanent once published**: changing it later means shipping a different app and losing every install, review and subscription. Play Billing verification is bound to the package name (section 90), so it must be settled *before* billing is configured, not after. |
| **Where it is configured** | `apps/mobile_flutter/android/app/build.gradle.kts` — `namespace` and `defaultConfig.applicationId`; the `MainActivity` package path; `PLAY_PACKAGE_NAME` in the backend environment. |
| **Validation** | `grep -r "com.example" apps/mobile_flutter/android` returns nothing; `GET /v1/admin/ops/provider-health` no longer reports `PACKAGE_ID_DECISION_REQUIRED` for `play`. |
| **Note** | No id has been invented. The spec and the prototype name none, and section 140 forbids guessing one. The suggested shape once a domain is chosen is `com.<org>.ecomsbd`. |

## 2. `RELEASE_SIGNING_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — operator decision** |
| **What is missing** | An upload keystore. Release builds currently sign with the debug key so `flutter run --release` works locally. |
| **Why it is required** | Play refuses a debug-signed upload, and the upload key is as permanent as the package id. |
| **Where it is configured** | `apps/mobile_flutter/android/key.properties` (gitignored) and the `release` signing config in `app/build.gradle.kts`. |
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

## 6. `SMS_PROVIDER_REQUIRED`

| | |
|---|---|
| **Status** | **BLOCKED — needs a provider choice and an account** |
| **What is missing** | A Bangladeshi SMS gateway, a registered sender id, and that gateway's segment and cost rules. |
| **Why it is required** | OTP sign-in currently uses the development provider, which writes codes to the log and **cannot start in production** — three independent guards refuse it. Without SMS there is no way for a real seller to sign in. This is the blocker that stops even a pilot. |
| **Where it is configured** | `OTP_PROVIDER=sms_gateway`, `OTP_PROVIDER_SECRET`, `SMS_TRANSPORT=sms_gateway`. |
| **What exists already** | The provider-neutral transport, payload validation, GSM-03.38/UCS-2 segment estimation, per-segment quota metering, Banglish templates, and delivery records. Section 22's rule holds: the provider's own segment count wins over our estimate, and both are stored. |
| **Validation** | `APP_ENV=production` boots (it refuses today); a real OTP arrives; `notification_deliveries` records the provider's segment count. |

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
| **Where it is configured** | `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE`. |
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
- **Point `PUBLIC_BASE_URL` at https.** Production refuses to start otherwise.

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
| Production config | `APP_ENV=production` boots. It fails fast on any placeholder secret, on the dev OTP provider, on non-https, and on SQLite. |
| Blockers | Every row above resolved or explicitly accepted, in writing, by the operator |
