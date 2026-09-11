# Flutter build-time configuration

`--dart-define` values are **compiled into the binary**. Anyone with the APK can
read them. That makes this directory a place for configuration, never for
secrets.

**Never put here:** a database URL or password, an R2 key, a Steadfast merchant
secret, the Apple `.p8`, a Google service account, an admin token,
`JWT_SIGNING_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, or a bKash credential.

The app has exactly one network peer: the ecomsbd backend. It never holds a
provider credential, never reaches a database, and never signs anything itself.
Every secret lives on the server, in `.env.production.local`.

## Building a release

```
cp env/production.example.json env/production.json     # gitignored
# fill in ECOMSBD_API_BASE_URL
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

## Files that are not dart-defines

| File | Where it goes | Why not here |
|---|---|---|
| `google-services.json` | `android/app/google-services.json` | The Firebase Gradle plugin reads it at build time. Gitignored. |
| `key.properties` | `android/key.properties` | Release signing. Gitignored, and the keystore itself must live outside the repository. |
| `applicationId` | `android/app/build.gradle.kts` | Gradle is the source of truth for the package id. It must match `PLAY_PACKAGE_NAME` on the backend. |
