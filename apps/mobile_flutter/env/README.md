# Flutter build-time configuration

`--dart-define` values are **compiled into the binary**. Anyone with the APK can
read them. That makes this directory a place for configuration, never for
secrets.

**Never put here:** a database URL or password, an R2 key, a Steadfast merchant
secret, the Apple `.p8`, a Google service account, an admin token,
`JWT_SIGNING_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, or a bKash credential.

The app reaches business data only through the ecomsbd backend. Native Google
and Apple SDKs contact their identity providers; their identity tokens are sent
to the backend for verification. Only the backend response establishes an
ecomsbd session, using the existing secure token store and refresh rotation.
OAuth client IDs are public configuration, not client secrets.

## Building a release

```
cp env/production.example.json env/production.json     # gitignored
# fill in ECOMSBD_API_BASE_URL and GOOGLE_CLIENT_ID_WEB
flutter build appbundle --release --dart-define-from-file=env/production.json
```

Or pass them individually, which is what CI does:

```
flutter build appbundle --release \
  --dart-define=ECOMSBD_API_BASE_URL=https://api.yourdomain.com
```

## What each key does

| Key | Read by | Notes |
|---|---|---|
| `ECOMSBD_API_BASE_URL` | `lib/core/env.dart` → `Env.apiBaseUrl` | Defaults to `http://10.0.2.2:8000`, the Android emulator's alias for the host machine. A release build must override it. |
| `ECOMSBD_SENTRY_DSN` | `lib/core/env.dart` → `Env.sentryDsn` | Declared, **not yet used**: `sentry_flutter` is not a dependency. Wiring it is part of `SENTRY_CONFIGURATION_REQUIRED`. |
| `PHONE_OTP_LOGIN_ENABLED` | `Env.phoneOtpLoginEnabled` | Defaults to `false`; hides the phone option and blocks both OTP routes. Keep false in production, matching the backend. OTP code is retained. |
| `GOOGLE_CLIENT_ID_WEB` | `Env.googleServerClientId` | Web OAuth client ID passed to Google's native SDK as `serverClientId`. Must match an allowed audience on the backend. Required for Google sign-in. |
| `GOOGLE_CLIENT_ID_IOS` | `Env.googleIosClientId` | iOS OAuth client ID, when an iOS runner is provisioned. Not needed for Android. |

## Provider setup and remaining external validation

- **Google / Android:** when configuring later, register `com.ecomsbd.app` and debug,
  release and Play App Signing certificate SHA fingerprints in the same Google
  project as the web OAuth client. Supply `GOOGLE_CLIENT_ID_WEB` at build time
  and in the backend's Google audience configuration. This integration passes
  `serverClientId` explicitly, so Firebase and `google-services.json` are not
  required. See the [official Android integration instructions](https://pub.dev/packages/google_sign_in_android).
- **Apple:** `sign_in_with_apple` requests an identity token on iOS/macOS and
  sends it to `/v1/auth/oauth/apple`. On Android, Windows and web the button
  reports that Apple is unavailable and offers Google/email instead; it does
  not launch an unsupported plugin or an unconfigured browser redirect.
  This repository currently has an **Android runner only**. Shipping Apple on
  iOS requires provisioning that runner, registering its bundle ID with Apple,
  enabling the Sign in with Apple capability/entitlement, and matching the
  backend `APPLE_CLIENT_ID` audience. See the [official Apple plugin setup](https://pub.dev/packages/sign_in_with_apple).
  Google on that future iOS runner also needs its client ID and reversed-client-ID
  URL scheme. Do not put Apple private keys, Team/Key secrets or OAuth client
  secrets in Dart defines; the backend's implemented Apple verification uses
  public JWKS and does not require a private key.
- **Email links:** existing backend browser pages handle verification and reset
  at `PUBLIC_BASE_URL/auth/email/verify#token=...` and
  `PUBLIC_BASE_URL/auth/password/reset#token=...`. Flutter also handles those
  paths with fragment/query tokens if handed a route, including missing/expired
  links. No verified Android App Links or iOS Universal Links are registered
  here; emails therefore use the working browser flow, then the seller returns
  to Sign In. Email delivery still requires the backend's transactional email
  provider configuration. Registration's pending screen supports resend and
  continuing to shop setup, consistent with the backend's pre-verification session.

Keep backend `EMAIL_PASSWORD_AUTH_ENABLED=true`, `GOOGLE_AUTH_ENABLED=true`,
`APPLE_AUTH_ENABLED=true`, and `PHONE_OTP_LOGIN_ENABLED=false`. No SMS or
Facebook configuration is needed. Real Google/Apple device sign-in remains a
release check once IDs, signing and the relevant platform runner are available.

## Files that are not dart-defines

| File | Where it goes | Why not here |
|---|---|---|
| `google-services.json` | `android/app/google-services.json` | Optional for a future Firebase integration; not required by this auth flow. Gitignored. |
| `key.properties` | `android/key.properties` | Private release signing config; environment overrides are also supported. See [key generation and fingerprints](../android/RELEASE_SIGNING.md). The keystore stays outside the repository. |
| `applicationId` | `android/app/build.gradle.kts` | Permanently `com.ecomsbd.app`, also the namespace/activity package. `PLAY_PACKAGE_NAME` must match when Play is configured. |
