# Flutter authentication configuration

Only public configuration belongs in `production.json` (ignored):
`ECOMSBD_API_BASE_URL=https://api.scalemyprints.com`, `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, `GOOGLE_CLIENT_ID_WEB`, and the optional native iOS client ID.
Build with `flutter build appbundle --release --dart-define-from-file=env/production.json`.

Supabase owns all seller credentials and sessions. Encrypted storage persists
the SDK session and PKCE verifier. Flutter sends the Supabase access JWT to
FastAPI and never reads business tables using Supabase. Existing shop/onboarding
routing uses `/v1/me`; shop changes never replace Supabase credentials.

Allow `com.smply.app://auth/callback` in Supabase's redirect configuration and set
Site URL `https://scalemyprints.com`. The Android intent filter handles both cold
and warm callbacks. Reset UI opens only after the SDK validates a recovery code.
Email confirmation requires the real Supabase SMTP/provider settings.

Google native authentication exchanges its ID token with Supabase. Configure
Android `com.smply.app` and signing fingerprints in the Google project. Apple
native authentication uses nonce binding and Supabase; Android safely reports
Apple unavailable. This repository has no iOS runner; shipping on iOS requires
runner provisioning, signing, callback URL scheme, and Apple capability.

Never add service-role, database, OAuth secret, R2, Cloudflare, Northflank,
Steadfast, encryption or admin credentials to these build values.
Phone OTP is disabled. See [Supabase setup](../../../docs/PRODUCTION_ENV_SETUP.md#5-supabase-auth).

Firebase reads `android/app/google-services.json`, verified for `com.smply.app`.
The Google Services plugin supplies Android options. Firebase initializes at
startup; authenticated sessions register refreshed FCM tokens through FastAPI.
Logout clears device tokens. The service account belongs only in backend cloud
secrets and must never be copied into Flutter.
