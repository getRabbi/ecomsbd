# Optional FUTUREGROW app telemetry

Code is on a review branch only. Nothing was deployed, migrated or released.

The Flutter app reuses the FUTUREGROW client from PR #32 (vendored under
`apps/mobile_flutter/packages/futuregrow_telemetry`, with close stopping future sends).
`lib/data/telemetry/futuregrow.dart` connects it to the existing authenticated API,
account-scoped durable preferences, foreground lifecycle and Privacy opt-in. A pending
offline withdrawal survives restart. Logging out or changing shops cannot forward another
account's queue. Android obtains the Play referrer through the official 2.2 client;
iOS obtains AdServices tokens at send time. Neither token nor server key is persisted on-device.

Activation means completed seller onboarding (authenticated shop membership). Conversions
mean **verified Ecoms BD subscription payments**, not the seller's customer orders. Only
the shop owner can report PURCHASE/RENEWAL billing records, using verified amounts/currencies
and provider timestamps. Transactions before the acknowledged installation are excluded;
each billing transaction binds to one installation to prevent cross-device double counting.
Product/privacy owners must approve these definitions and bilingual consent copy before release.

`backend/app/api/v1/telemetry.py` uses existing authentication, tenant context, Redis and billing
models. Consent defaults denied; unknown/lost server state denies processing. Use durable Redis
in production. Install acknowledgements and consent are server-side; the mobile outbox owns
retry scheduling. Withdrawal stops future relay sends; retention/deletion of already delivered
FUTUREGROW evidence remains subject to its consent/DSAR workflow.

External setup (not performed): set `FUTUREGROW_BASE_URL` to the approved HTTPS origin and
`FUTUREGROW_SERVER_KEY` in backend secrets; register `com.ecomsbd.app` under the correct tenant;
approve campaign links and actual platform identities. Missing configuration returns 503.
No ingestion key enters Flutter build variables. No new database migration or live schedule.

Separate release acceptance still requires Android/iOS compilation, genuine Play referrers,
an authorized production AdServices response, consent withdrawal across devices, and a verified
subscription through the real billing provider. Fixture tests do not establish operational status.
