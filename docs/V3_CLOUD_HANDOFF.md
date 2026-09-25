# V3 cloud-session handoff

Short state note for the next session. Git is the source of truth; check it first.

## Where things are

- Completed phases on `main`:
  - **V3.1 Integrations Hub**: merge commit `99dd98e` (PR #1).
  - **V3.2 advanced two-way sync**: merge commit `5eef579` (PR #2).
  - **Security: `alembic_version` client-role revoke** (`a32002`): merge commit `049ee3e` (PR #3).
  - **V3.3 Messaging & Campaigns** (`a33001`): merge commit `8403c7e` (PR #4); see `docs/V3_3_MESSAGING_CAMPAIGNS.md`.
  - Handoff note for V3.3: merge commit `ac90078` (PR #5).
  - **V3.4 Automation Pro** (`a34001`): merge commit `370432a` (PR #6), CI green on head
    `1f0071e`; see `docs/V3_4_AUTOMATION_PRO.md`. Post-merge review: no blocking defect.
  - **V3.5 Inventory + Procurement** (`a35001`): merge commit `dbf4de3` (PR #7), CI green on
    head `3eb2bd3`; see `docs/V3_5_INVENTORY_PROCUREMENT.md`.
  - **V3.6 Forecasting & Advanced Intelligence** (`a36001`): merge commit `91e776b` (PR #9), CI
    green on head `36675e5`; see `docs/V3_6_FORECASTING.md`.
  - **V3.7 External Risk + Network Intelligence** (`a37001`): merge commit `99e606f` (PR #11),
    CI green (PostgreSQL included) on head `7309d9e`; see `docs/V3_7_RISK_NETWORK.md`.
  - **V3.8 Developer Platform** (`a38001`): merge commit `5e0bb38` (PR #12), CI green
    (PostgreSQL and the SDK step included) on head `990423a`; see `docs/PUBLIC_API.md` and `sdk/`.
  - `99e606f` (V3.7 merge) failed its main CI at the type check only: SQLAlchemy 2.1.0 was
    released between the PR run and the merge. `990423a` caps it at `<2.1` (CI and the image
    install unpinned).
- **Final V3 RC** (on `5e0bb38`, whose tree is identical to the one tested locally):
  - backend SQLite 1701 passed / 5 skipped, plus ruff, format and mypy clean;
  - Flutter 407 passed, `flutter analyze` clean;
  - web typecheck, lint, production build and unit tests;
  - gitleaks over 152 commits: clean; one Alembic head `a38001`;
  - main CI on `5e0bb38`: PostgreSQL suite, migration from empty, dependency audit, backup
    restore drill, backend image, secret scan and Flutter debug APK.
- **Real-device V3 RC: PASS** (2026-09-25). POCO X3 NFC, Android 11, with a profile build using
  `env/production.json` against production. Every V3.1–V3.8 mobile screen was checked in EN and BN.
  The API log had no 404 or 5xx. It found three defects, fixed in PR #15 (merge `a069621`):
  - WhatsApp showed the raw key `int.provider.WHATSAPP`.
  - Forecasts told shops without sales history "nothing will run out".
  - Automation briefly showed an owner the read-only note, and kept a stale "Turned on" notice.
- **`V3_PRODUCTION_READY = YES`, `REAL_DEVICE_V3_RC = PASS`. No V3 code blocker remains.**
- **V3 is tagged and released as `v3.0.0`** (Android `3.0.0+300`, merge `77d497f`,
  2026-09-25). Google Play distribution is still pending; see Release.
- Production is aligned with V3.8: the DB is at `a38001` (2026-09-25; see Production).
- PRs #1–#14 passed full CI before merging: backend lint, types, SQLite and **PostgreSQL**
  tests, PostgreSQL migration-from-empty, dependency audit, Flutter, secret scan. PR #15
  (mobile only) was verified locally (Flutter 413 passed) because GitHub Actions did not
  start jobs (account billing hold).
- Work from **`main`**. `v3.1-integrations` and `v3.2-sync` are merged; new work goes on a new branch.
- No release tag has been created for V3.1 or V3.2.
- Migration head: **`a38001`**, one head, no branches (`a31001` = V3.1, `a32001` = V3.2,
  `a32002` = client roles lose access to `alembic_version`, `a33001` = V3.3, `a34001` = V3.4,
  `a35001` = V3.5, `a36001` = V3.6, `a37001` = V3.7, `a38001` = V3.8).

## Production

- **Prod DB is at `a38001`** (current head). The API and worker were last confirmed on
  `a069621` (main after PR #15; backend unchanged since `68744fe`). PRs #16 and #17 changed no
  backend code. After #17 merged, `/health/ready` was 200 with PostgreSQL and Redis ok.
- Migrated on 2026-09-25 around 06:46 UTC with the owner's approval: `a32001` → `a32002` →
  `a33001` → `a34001` → `a35001` → `a36001` → `a37001` → `a38001`, in one
  `alembic -x database-only=true upgrade head` transaction.
- Checked afterwards:
  - `alembic_version` has one row, and every new table and column exists.
  - `anon` and `authenticated` have no privileges on `alembic_version` or the new tables, and
    RLS is on.
  - The new API image went ready by itself and replaced the `7ec1071` container.
  - V3.3–V3.8 routes answer 401 when logged out, not 404.
  - The worker logs show no schema errors, and the outbox is clear.
- Northflank auto-deploys `main`. `/health/ready` needs DB == code head, so a new image with a
  new migration stays unready and the old container keeps serving until prod is migrated.
  Migrate right after such a merge builds.
- **How to migrate:** run it through the session pooler (5432), with only `DATABASE_URL` loaded:
  `DATABASE_URL='postgresql+asyncpg://...5432/postgres' alembic -x database-only=true upgrade head`.
  - The session pooler allows 15 clients, and the API and worker pools can fill it
    (`EMAXCONNSESSION`).
  - If it is full, pause **only the worker** with Northflank `POST .../services/ecomsbd-worker/pause`,
    migrate, then `POST .../resume`. The worker keeps its instance count. Do not stop the
    serving API.
  - Do not use the transaction pooler (6543) for migrations.
  - Cloud sessions have no production credentials; the owner or a local session runs it.
- Worker jobs added across V3.3–V3.8, all live now:
  - `run_campaigns` (every minute);
  - `scan_segment_entries` (daily);
  - `snapshot_demand_forecasts` (daily 02:50 UTC);
  - `prune_external_risk_lookups` (daily 04:20 UTC);
  - cohort cells written by the monthly `build_network_benchmarks`.
- What each migration changed:
  - `a32002`: privileges only.
  - `a33001`: two tables.
  - `a34001`: two tables, plus a data step that published existing rules as version 1 and
    renamed run statuses.
  - `a35001`: eleven tables.
  - `a36001`: `demand_forecasts` and `suppliers.lead_time_days`.
  - `a37001`: three tables.
  - `a38001`: `public_api_keys.expires_at`.

  Every new table has RLS on and client grants revoked.

## Release (`v3.0.0`)

- PR #17 bumped the app to `3.0.0+300` and changed only `pubspec.yaml`. The highest
  versionCode before it was 232. GitHub Actions still did not start jobs (billing hold).
- Annotated tag `v3.0.0` points to merge `77d497f`. The GitHub Release "ecomsbd v3.0.0" is
  marked Latest and has the APK, the AAB and `SHA256SUMS.txt`:
  - `ecomsbd-3.0.0+300-production.aab`: `19783b500fdd16119ab3889d2a31a873e4885cdc5bf7edff40420c9da99bab4d`
  - `ecomsbd-3.0.0+300-production.apk`: `45042e688a8555e569c27dbb045f43a4a6b6b1a77b97422027d4435ef32dee4a`
- Both were built from the tree of `77d497f` with `--dart-define-from-file=env/production.json`.
  They are signed with the upload key `CN=ecomsbd Upload`, the same key as v2.3.1 (SHA-1
  `A6:65:21:D5:5B:86:A6:C4:A7:6F:24:DD:DF:A7:23:8A:8A:E2:12:6F`).
- Checked: not debuggable, cleartext traffic off, and the only API endpoint is
  `https://api.scalemyprints.com/v1`. Native libraries are 16 KB aligned, targetSdk is 36,
  and no sensitive permissions are requested.
- Use the GitHub Release copies. The owner's ignored `release/v3.0.0/` holds the same files.

Still pending, all in the owner's consoles:

- **Play Internal testing.** Upload the release AAB, not the APK, with Play App Signing
  (Google-generated key). Nothing is on Play yet.
- **Play App Signing fingerprints.** After the upload they are under Setup → App integrity →
  App signing → "App signing key certificate". This key is not the upload key above.
- **Android Google Sign-In.** Create Android OAuth clients for `com.smply.app` in Google Cloud
  project **939255107253**, the project of web client `939255107253-vu8012mb…`: one with the
  Play app-signing SHA-1 and one with the upload SHA-1.
  - It is the right project: Supabase's `/auth/v1/authorize?provider=google` redirects with
    that client, the app passes it as `serverClientId`, and web Google login works with it.
  - Do not add these SHA-1s to Firebase `ecomsbd-11bdb`. It is used only for FCM, and Firebase
    would create the OAuth clients in the wrong project and claim the package + SHA-1 pair.
  - No rebuild and no Supabase change are needed. The app sends no nonce, so Supabase checks
    only the token audience, which already matches.
- **Play-installed smoke.** Install from the Internal testing link. Check launch, email login,
  Google login, session restore after a force-close, and Home. The POCO test phone has a
  debug-signed install that Play cannot update in place.
- **Before closed testing or production.** Play needs:
  - a hosted privacy policy URL;
  - a web URL for account deletion (the app has in-app deletion, but neither page exists);
  - the Data safety form, content rating, target audience and ads declaration;
  - app access (a reviewer login) and the store listing.

  A personal developer account created after November 2023 also needs a closed test with at
  least 12 testers for 14 days before production access.
- No public rollout until Google Sign-In passes on a Play-installed build.

## External gates (code is done, these are not)

- Shopify: `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` from an approved Shopify app,
  protected-customer-data access, app redirect and compliance webhook URLs.
  V3.2 features ask for extra scopes and prompt a reconnect.
- Meta/Messenger: `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` and
  App Review (`pages_messaging`). Messenger stays health only; marketing needs Meta's
  per-person marketing opt-in (not built).
- WhatsApp (V3.3): the same three Meta settings, plus each shop's own WABA (phone number ID,
  WABA ID, system-user token) subscribed to the ecomsbd Meta app. Embedded Signup / Tech
  Provider onboarding is not built. Only Meta-approved templates are sent; Meta bills
  marketing templates to the shop.
- Email receipts (V3.3): `EMAIL_WEBHOOK_SECRET` (Resend `whsec_…`) and a Resend webhook to
  `/v1/webhooks/messaging/resend`. Open tracking on the Resend domain is optional.
- WooCommerce: verified against a fake store in tests; **live-store verification pending**.
- External risk provider (V3.7): the provider-neutral layer is complete, but the adapter
  registry is empty. Activation needs a licensed provider with a documented contract and a
  reviewed adapter. Until then every shop sees `GATED`, and the first-party Risk Check is
  unaffected.
- RedX: a real positive merchant-token verification is still pending.

## Next phase

- V3 roadmap code is complete with V3.8 and released as `v3.0.0`; next is Play distribution
  (see Release). Deferred from V3.6:
  seasonality/weekday profiles, per-location forecasts, promotion effects. Deferred from V3.5:
  batch/expiry/serial tracking, per-location sync to storefronts, reservations shown to
  sellers, landed cost and averaging.
  Still out of scope: double-entry accounting, MRP, vendor portal, automatic purchasing,
  SDK/app marketplace.
- Post-release polish (not blockers, from before V3; found in the device RC):
  - the menu shows "Owner · " when the account has no phone;
  - sign-out takes about 4 s with no progress UI;
  - Order sources and Messaging show raw codes (`CUSTOM_PUSH`, `EMAIL`);
  - Home's "with couriers" amount (delivered, unpaid) and Cash & COD's "on the road" (booked
    consignments) use different definitions.

## Invariants

- Reuse the V2/V3 foundations. Do not build a second CRM, public API, webhook system,
  automation engine (V3.4 workflows extend the V2 rules), order domain or integration model,
  risk score (V3.7 provider facts stay beside the first-party Risk Check), or health model
  (the Developer portal reads the Integrations Hub views).
- The financial ledger and the stock ledger stay authoritative. Integrations never
  overwrite them: stock changes are ledger movements, and ambiguity becomes a conflict.
- Every outbox topic needs a handler, or its events block the head of the outbox.
- Tenant isolation, encrypted provider credentials, server-side RBAC, idempotency keys.
- Official provider APIs only; no invented provider statuses or tracking links.

## Local working tree

- `apps/web/.gitignore` has an unrelated local change owned by the user.
  Do not commit, revert or reformat it.
- Root `.env.*` files, `backend/.env`, `apps/mobile_flutter/env/production.json`,
  keystore/Firebase/OAuth JSON files are local secrets/artifacts, ignored on purpose.
