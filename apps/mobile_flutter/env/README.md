# Flutter authentication configuration

Only public configuration belongs in `production.json` (ignored):
`ECOMSBD_API_BASE_URL=https://api.scalemyprints.com`, `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, `GOOGLE_CLIENT_ID_WEB`, and the optional native iOS client ID.
Build with `flutter build appbundle --release --dart-define-from-file=env/production.json`.

Supabase owns all seller credentials and sessions. Encrypted storage persists
the SDK session and PKCE verifier. Flutter sends the Supabase access JWT to
FastAPI and never reads business tables using Supabase. Existing shop/onboarding
routing uses `/v1/me`; shop changes never replace Supabase credentials.

Allow `com.ecomsbd.app://auth/callback` in Supabase's redirect configuration and set
Site URL `https://scalemyprints.com`. The Android intent filter handles both cold
and warm callbacks. Reset UI opens only after the SDK validates a recovery code.
Email confirmation requires the real Supabase SMTP/provider settings.

Google native authentication exchanges its ID token with Supabase. Configure
Android `com.ecomsbd.app` and signing fingerprints in project `939255107253`
(`fashionos-3d779`). Keep the existing web/server client ID unchanged. This is
separate from the Firebase project used for FCM. On iOS the native client is
`939255107253-gi6clm3kdbr2a00qoekr9aor3mq1r2le` (bundle `com.ecomsbd.app`,
same project): `GOOGLE_CLIENT_ID_IOS` here, `GIDClientID` and its reversed URL
scheme in `ios/Runner/Info.plist`. Sign in with Apple is native on iOS only,
with nonce binding through Supabase; the Supabase Apple provider must list
`com.ecomsbd.app` as a client ID. Android does not show the Apple button.

Never add service-role, database, OAuth secret, R2, Cloudflare, Northflank,
Steadfast, encryption or admin credentials to these build values.
Phone OTP is disabled. See [Supabase setup](../../../docs/PRODUCTION_ENV_SETUP.md#5-supabase-auth).

Firebase reads `android/app/google-services.json` on Android and
`ios/Runner/GoogleService-Info.plist` on iOS (both ignored; Codemagic writes the
iOS one from its `ecomsbd_ios` group; see `codemagic.yaml`). Download them from the existing
FCM project `ecomsbd-11bdb` (`191767568417`) after registering `com.ecomsbd.app`.
Do not edit a config for the old package to claim it belongs to the new package.
The Google Services plugin supplies Android options. Firebase initializes at
startup; authenticated sessions register refreshed FCM tokens through FastAPI.
Logout clears device tokens. The service account belongs only in backend cloud
secrets and must never be copied into Flutter.

See [Android package migration](../../../docs/ANDROID_PACKAGE_MIGRATION.md)
for provider setup, signing fingerprints, and release validation gates.
