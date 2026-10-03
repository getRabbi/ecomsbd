# WhatsApp Embedded Signup v4

Checked against Meta's official documentation on 2026-10-03. This replaces
public seller credential entry and reuses the existing WHATSAPP connection,
encrypted vault, health checks, disconnect, templates, receipts, STOP handling
and chat-to-order pipeline. No migration or additional integration system.

## Manual Meta configuration

**MANUAL_META_VALUE_REQUIRED:** `META_WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID`.
In the existing app, open **Meta Developer Dashboard → Facebook Login for
Business → Configurations → Create configuration**. Select **WhatsApp Embedded
Signup** as the login variation and **WhatsApp Cloud API** as the product;
create a **v4** configuration. Copy its **Configuration ID** into Northflank.
Meta also offers the “WhatsApp Embedded Signup Configuration With 60 Expiration
Token” template. Inspect its selections; keep only WhatsApp Account/phone assets
and the permissions below. Never invent an ID or put it in Flutter.

- `whatsapp_business_management`: WABA settings, subscription, templates.
- `whatsapp_business_messaging`: phone registration and messaging.

In **Facebook Login for Business → Settings → Client OAuth settings**, enable
Client OAuth Login, Web OAuth Login, Enforce HTTPS, Embedded Browser OAuth Login,
Strict Mode for redirect URIs, and Login with the JavaScript SDK. Configure:

- Allowed SDK domain: `https://scalemyprints.com`.
- Valid redirect URI: `https://scalemyprints.com/ecomsbd/whatsapp-connect`.
- Retain Messenger redirect `https://api.scalemyprints.com/v1/integration-callbacks/meta`.

Keep `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` on the backend.
Use `PUBLIC_WEB_URL=https://scalemyprints.com` and include that origin in
`CORS_ALLOW_ORIGINS` without removing existing authorized origins. The existing
`META_GRAPH_API_VERSION` (v26.0) is used throughout. On the WhatsApp Business
Account webhook object, subscribe **messages** and **account_update** using
`https://api.scalemyprints.com/v1/webhooks/integrations/meta` and the existing
verify token. Account updates are acknowledged; assets are resolved via Graph.
The existing message receiver and signature verification are unchanged.

## Public approval and development access

Public signup requires **Business Verification**, **Tech Provider** onboarding
(or applicable Solution Partner arrangement), App Review and **Advanced Access**
for both WhatsApp permissions, and the appropriate Live app mode. Meta's current
Tech Provider guide states that WABA access without Advanced Access returns
error 200. App Review needs evidence of sending a message and creating a
template; API Setup/WhatsApp Manager recordings are permitted demonstrations.

`business_management` is **not requested or required here**. The current Cloud
API overview lists the two WhatsApp permissions. Additional business operations
such as a Solution Partner sharing its credit line need other permissions;
this implementation does not perform them. Tech Provider customers add their
own WhatsApp Manager payment method for billed messaging. Connected does not
claim that billing or template approval is complete. This is standard Cloud
API signup, not Coexistence or number migration.

After Meta actually grants access, an operator sets
`META_WHATSAPP_PUBLIC_SIGNUP_ENABLED=true` (default false). This is a deployment
gate, not evidence of Meta approval; the software does not fabricate a review
status. `META_WHATSAPP_TEST_USER_IDS` is a JSON array of **ecomsbd user UUIDs**
allowed to attempt development signup before public access. Their Meta accounts
still need the admin/developer/tester roles and permissions Meta requires.

| Condition | Result |
|---|---|
| Global settings, hosted origin or config ID missing | `META_APP_SETUP_REQUIRED`: temporarily unavailable |
| Public access unconfirmed and seller not allowlisted | `META_APPROVAL_REQUIRED` |
| Setup ready, seller has no connected account | Seller not connected; Connect with WhatsApp |
| Credentials expired/revoked or permissions withdrawn | `AUTH_EXPIRED`: Needs reconnect |

## Handoff and completion

1. Authenticated seller with `settings.manage` posts
   `/v1/integrations/{id}/connect`. A 10-minute one-use ticket is bound to the
   existing WhatsApp row, tenant and initiating user. New signup replaces it.
2. Mobile opens `/ecomsbd/whatsapp-connect#state=…`. Only the launch ticket is in
   the fragment; no seller JWT or Meta token. The page removes it before SDK
   loading and sends it in an exact-origin JSON POST to
   `/v1/integration-callbacks/whatsapp-signup/bootstrap`.
3. Bootstrap consumes the ticket and returns public SDK configuration and a
   scoped browser capability kept only in memory. There is no ambient cookie
   auth or localStorage/sessionStorage persistence.
4. Official `FB.login` uses `config_id`, `response_type: code`,
   `override_default_response_type: true`, and v4 `extras: {setup: {}}`.
   Exact HTTPS Meta message origins are checked. The SDK callback immediately
   POSTs the code to `/complete`; Meta documents a **30-second code TTL**.
5. Backend rechecks expiry, connection binding and active initiating seller/shop
   membership, then durably claims the attempt. It uses Meta's official
   `GET /oauth/access_token` business-token exchange, separate from Messenger's
   user-token upgrade. No raw response is logged or returned to the app.
6. `GET /debug_token` verifies app identity, token validity/expiry, scopes and
   granted WABA targets. A WABA GET and paginated `/phone_numbers` verify phone
   membership. Browser IDs must agree; absent IDs resolve only if unambiguous.
7. Verify phone state; register if needed through `POST /{phone}/register` with
   a server-generated, vault-encrypted six-digit PIN; verify registration.
   Subscribe through `POST /{waba}/subscribed_apps`. Only successful checks and
   subscription allow the existing lifecycle to mark Connected and store its
   encrypted credentials.
8. Exact duplicate completion returns its stored safe result without another
   code exchange. Changed input/replay is refused. Disconnect or new signup
   invalidates old capabilities. An interrupted claimed attempt must restart
   rather than blindly retry an ambiguous code exchange.
9. Success uses the existing HTTPS return endpoint and
   `com.ecomsbd.app://integrations/return?...`, with **ecomsbd খুলুন** fallback.
   Flutter refreshes the backend; deep-link hints cannot create fake success.

Meta's documented GET exchange/debug calls carry secret parameters only on
server-to-server requests, never browser URLs. Central redaction covers code,
client-secret, input-token and state queries and sensitive request fields.
Provider errors become safe codes; Sentry frame locals remain removed.
Disconnect erases local credentials/routing. It does not delete the WABA,
deregister its phone or unsubscribe other connected numbers sharing the WABA.

## Operator-only manual test-number path

The existing `POST /v1/integrations/{id}/whatsapp` still accepts
`phone_number_id`, `waba_id`, `access_token`. It requires a shop-authorized seller
session **and** independent `X-Admin-Token` with `admin.repair_run`. A seller JWT
or client boolean cannot enable it. Normal mobile/web screens have no fields.
Local/test may explicitly set `META_WHATSAPP_MANUAL_ENABLED=true`; the flag is
ignored in staging/production, where admin authentication remains required.
Use a protected request file/operator client; never put secrets in URLs or shell
history. Credentials use the existing vault, preserving the Meta API Setup test
number path.

Real test: connect the Meta test number via the admin path, use an allowed sender,
send a four-message order burst and repeat one delivery. Check ONE Inbox draft,
review and confirm. Once configuration/Meta access permits, repeat after Embedded
Signup from an allowlisted seller. Mocked tests are not evidence of real WhatsApp
delivery or public Meta approval.

## Official references

- [Implementation/configuration](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/implementation)
- [Server completion](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-customers-as-a-tech-provider)
- [Overview/permissions](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview)
- [Tech Provider/App Review](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/get-started-for-tech-providers)
- [Shared WABA verification](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/manage-accounts)
- [Phone registration](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-phone-number/register-api)
