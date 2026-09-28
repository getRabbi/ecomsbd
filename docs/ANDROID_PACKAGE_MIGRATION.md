# Android production package migration

The production Android identity is `com.ecomsbd.app`, starting with the planned
`3.0.3+303` release. The user-facing brand remains **ecomsbd**. The existing
upload key, Supabase project, production API, and Free Launch behavior stay in
place. Version 303 is absent from repository history and local release artifacts;
its availability in Play Console must also be confirmed before building.

## Package reference audit

| Class | Location | Action |
| --- | --- | --- |
| A: Android runtime | `apps/mobile_flutter/android/app/build.gradle.kts` | Set `applicationId` and namespace to `com.ecomsbd.app`. |
| A: activity | `android/app/src/main/kotlin/com/smply/app/MainActivity.kt` | Move under `com/ecomsbd/app` and update the declaration. |
| B: Firebase | Root and app-module `google-services.json` (private, ignored) | Keep the old files intact until a genuine config for the new registration is downloaded. Never rewrite package values. |
| B/D: CI Firebase fixture | `.github/workflows/ci.yml` | Align the existing synthetic debug-only fixture with the new ID. It is not a production registration or a device-test config. |
| C: auth callback | Android manifest, `lib/core/env.dart`, `lib/data/auth/supabase_bootstrap.dart` | Use `com.ecomsbd.app://auth/callback` consistently. |
| D: auth tests | `test/data/auth_flows_test.dart` | Exercise the new callback and reject a callback for the old installation. |
| D: Play configuration | `.env.production.example` and deployed `PLAY_PACKAGE_NAME` | Align with the new application ID; keep billing disabled. |
| B/C: active operator guidance | `env/README.md`, `android/RELEASE_SIGNING.md` | Document the new identity, correct provider projects, and existing signing key. |
| E: historical reports | `README.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/V3_CLOUD_HANDOFF.md`, `docs/RELEASE_READINESS.md`, `docs/PRODUCTION_ENV_SETUP.md`, `release/v3.0.2/FREE_LAUNCH_VERIFICATION.md` | Retain old release/setup history. This guide supersedes their Android package instructions. |
| E: private previous-run artifacts | Ignored `.env.*` one-off migration/recovery helpers, smoke scripts, logs, screenshots, and previous APK/AAB output | Preserve as evidence of the old installation; do not use them as validation of this release. |

The Dart package remains `ecomsbd`. No `assetlinks.json`, HTTPS App Links,
custom content provider, or hard-coded provider authority exists in app source.
AndroidX Startup and Firebase provider authorities come from library manifests
using `${applicationId}`. The release APK's merged manifest resolves exactly
`com.ecomsbd.app.androidx-startup`, `com.ecomsbd.app.firebaseinitprovider`, and
`com.ecomsbd.app.flutterfirebasemessaginginitprovider`; no FileProvider is present.
Recheck these in the final bundle rather than relying on stale generated files.

## Firebase and notifications

The production FCM project is **ecomsbd-11bdb**, number **191767568417**.
It is separate from the Google Sign-In OAuth project below.

The new Android app was registered through the official Firebase Management API
and its unmodified config downloaded on 2026-09-28. Firebase app ID:
`1:191767568417:android:53e39112fdcff2bc208360`. The old registration is retained.
The downloaded config stays ignored and private to this checkout. A fresh
checkout must download the existing new app's config, not create a duplicate.

If the app-module config has no Android client for `com.ecomsbd.app`, the release
gate is **FIREBASE_ANDROID_APP_REQUIRED = com.ecomsbd.app**. In Firebase Console:

1. Open the existing production project `ecomsbd-11bdb`.
2. Add app → Android → package `com.ecomsbd.app`.
3. Download the genuine `google-services.json` and place it at
   `apps/mobile_flutter/android/app/google-services.json`.
4. Verify the project number and matching Android client before any device or
   release build. Keep the old registration for existing installations.

The Google Services plugin supplies Firebase options. Firebase initialization,
permission requests, token acquisition/refresh, and authenticated `/v1/auth/device`
registration are unchanged. The new Android sandbox creates a new install ID and
FCM token; never copy session storage or tokens from the old package.

Notification metadata retains `@drawable/ic_stat_ecomsbd` and
`@color/ecomsbd_notification`. The current transport sends FCM notification plus
data payloads; the SDK displays background notifications and uses its fallback
channel when no channel is specified. There is no custom channel or Dart
`onMessageOpenedApp`/`getInitialMessage` route handler in the starting code.
Specific notification tap routing is therefore not proven by this migration;
do not claim it passed without a real-device result or expand business logic
as part of the package rename.

Reference: [Firebase Android registration and config download](https://firebase.google.com/docs/android/setup).

## Google Sign-In and Supabase callbacks

In Google Cloud project **fashionos-3d779 (939255107253)**, open
[Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients?project=fashionos-3d779).
Reuse matching Android clients, or create the missing clients:

| Build | Package | SHA-1 |
| --- | --- | --- |
| Release APK / upload key | `com.ecomsbd.app` | `A6:65:21:D5:5B:86:A6:C4:A7:6F:24:DD:DF:A7:23:8A:8A:E2:12:6F` |
| Debug/profile, only if used for local Google Sign-In | `com.ecomsbd.app` | `7F:E9:37:21:EB:9D:B1:BE:EF:EF:F8:9B:62:D5:E0:E2:2C:DF:54:86` |
| Play-distributed installation | `com.ecomsbd.app` | Obtain the actual Play App Signing SHA-1 after Play accepts the AAB. |

The debug fingerprint above was read from the actual local debug keystore.
Do not register these OAuth identities in the FCM project instead. Keep the
existing Android clients for the old package.

Keep `GOOGLE_CLIENT_ID_WEB` / native `serverClientId` unchanged:
`939255107253-vu8012mb328h36jjagdvbos9mjltruug.apps.googleusercontent.com`.
Do not change the Supabase Google provider credentials or project.

In the existing Supabase project's Authentication → URL Configuration, add
`com.ecomsbd.app://auth/callback` to the redirect allowlist while retaining the
old callback for old installations. Preserve the existing Site URL and Google
provider callback URL. This affects confirmation, recovery, and hosted PKCE
flows; native Google authentication exchanges an ID token with Supabase.
See [Supabase redirect URL configuration](https://supabase.com/docs/guides/auth/redirect-urls).

This redirect addition was applied and read back from the production Management
API on 2026-09-28. Existing redirects, Site URL, Google web client ID, and Google
secret were preserved. Production `PLAY_PACKAGE_NAME` was also changed to
`com.ecomsbd.app`; all other production secret-group values were preserved,
including `FREE_LAUNCH_MODE=true` and `BILLING_ENABLED=false`. Readiness returned
200 with healthy PostgreSQL and Redis after the change.

## Release gates

1. Confirm that Play has never used versionCode 303; otherwise choose the next
   safe integer and update the artifact name. Do not reuse 302.
2. Validate the real Firebase config, run `flutter analyze` and the focused
   auth/notification/Free Launch/navigation/offline tests, then inspect the
   actual merged release manifest. A Gradle dry run is not manifest validation.
3. Build a release APK using `--dart-define-from-file=env/production.json`, install
   beside the old package, and inspect launcher, splash/animation, authentication
   and session restoration, Home, Orders, Money, Insights, Settings, Free Launch,
   offline recovery, and notification initialization. Preserve the old app/session.
4. Confirm `https://api.scalemyprints.com/v1`, `FREE_LAUNCH_MODE=true`,
   `BILLING_ENABLED=false`, and the existing Supabase and FCM projects.
5. Only after package/config validation passes, run from `apps/mobile_flutter`:

   ```powershell
   flutter build appbundle --release --dart-define-from-file=env/production.json
   ```

6. Inspect the resulting bundle: package/version, non-debuggable release,
   permissions, target SDK, production defines, genuine Firebase resources,
   provider authorities, 16 KB native-library alignment, and signature matching
   `CN=ecomsbd Upload` with the upload SHA-1 above.
7. Copy the verified bundle to
   `release/v3.0.3/ecomsbd-3.0.3+303-production.aab`, calculate SHA-256, and record
   its source commit/tree and private build-config hashes. Use the actual build
   number if it changes. Compare the final main source tree with this source;
   rebuild if it differs.
8. Keep incomplete work in a draft PR. Merge only when required checks and
   release gates are satisfied. The owner uploads manually; do not upload or
   start a rollout automatically.
