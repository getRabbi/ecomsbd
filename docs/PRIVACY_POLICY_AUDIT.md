# Public privacy policy audit — 29 September 2026

**Draft: do not merge/publish until a real privacy contact has been added to
both language versions and production DNS is corrected.** There is currently
no contact section; the draft's contact references must be resolved before release.

Scope: `com.ecomsbd.app`, the current Next.js web client, FastAPI/worker data
paths, and read-only production provider configuration. Historical `PRIVACY.md`
and release documents contain stale claims and are not the authority for this policy.

## Evidence and wording

| Practice | Implementation/configuration checked | Policy treatment |
| --- | --- | --- |
| Email/password and Google sign-in | Mobile `data/auth/auth_repository.dart`; web `lib/supabase.ts`; backend `auth/supabase.py`, `auth/identities.py`, `users/models.py` | Supabase Auth and optional Google sign-in; identity/profile/session data. No Google password access. |
| Shop and team | `tenants/models.py`, `tenants/invitations.py`, `api/v1/team.py` | Shop/pickup details, settings, invitations, memberships and roles. |
| Customer and commerce | `customers/models.py`, `customers/crm_models.py`, `orders/models.py`, `products/models.py`, `consignments/models.py` | Phones, addresses, notes/tags, original pasted text, order snapshots, products/variants, inventory, returns/RTO. |
| Procurement | `procurement/models.py` | Supplier contact data, purchase orders, receiving, payables and payment records. |
| Business finance | `ledger`, `money`, `payouts`, `reconciliation`, `expenses`, `profit` models/services | COD, receivables, payouts, references, statements, costs, cashflow and profit. This is business recordkeeping, not subscription payment processing. |
| Free launch | Live Northflank secret group, read 29 September | `FREE_LAUNCH_MODE=true`, `BILLING_ENABLED=false`; no current card/bKash/Nagad subscription credential collection. |
| Couriers | `couriers/adapters`, `api/v1/couriers.py`, provider capability manifests | Steadfast/Pathao/RedX sharing conditional on available, connected and used functionality. No claim that every shop has an active connection. |
| Commerce connections | `integrations/{shopify,woocommerce,custom_website,receiver,inbound,outbound,catalog,inventory}.py` | Credentials, store identifiers, selected order/catalog/stock/status data and webhook/sync records. |
| Meta and messaging | `integrations/meta.py`, `integrations/whatsapp.py`, `messaging/{models,providers,receipts,campaigns}.py` | Templates, recipients, content, consent, campaign selections and supported receipts. Messenger connection health only; not a stored/read inbox or enabled marketing channel. |
| Provider gates | Live configuration: no Shopify/Meta setup credentials; business `EMAIL_TRANSPORT=disabled` | Gated providers are not named as active data recipients. Resend is described as conditional. SMS sending unavailable. |
| First-party/network risk | `analytics/{rto,network,network_privacy}.py`, `risk_providers/registry.py` | Own-shop history; empty external provider registry; opted-in aggregate benchmarks with cohort/dominance/rounding restrictions. No shared identifiable blacklist. |
| Devices and diagnostics | `auth/models.py`, `api/middleware.py`, `core/observability.py`, mobile device/auth/push code | Installation/device details, push tokens, request/session/security metadata and operational logs. Application IP handling includes hashing; do not promise infrastructure never processes raw IPs. |
| Analytics/crash | Web/mobile dependencies and initialization; live Sentry DSN absent | No third-party product analytics, ads tracking or replay. No Firebase Analytics/Crashlytics integration. Backend Sentry disabled. Business analytics are calculated internally. |
| Files | `common/object_storage.py`, imports, exports and payouts services; live R2 configured | Private R2 import/payout/export objects. Do not promise every file is purged on deletion. |
| Hosting | Live Northflank configuration; Vercel project API; domain HTTP response | Supabase database/auth, Northflank API/worker/Redis, Vercel web, Cloudflare network/R2, Google sign-in and FCM. No Bangladesh-only storage claim. |
| Security | `core/crypto.py`, tenant/role guards, integration credential serializers, public API key generation | Supported field-level encryption/masking; one-time generated secrets distinguished from stored credential readback; no blanket encryption guarantee. |
| Local data/permissions | Mobile Drift/outbox, secure session storage, Android manifest; web session provider | Offline data, secure session credentials, auth cookies/language storage, user-selected files. No contacts/SMS/call-log/location/camera/microphone permission requests. |

## Deletion and retention: material limits

Read `backend/app/privacy/service.py`, `privacy/models.py`,
`api/v1/account.py`, `common/object_storage.py` and mobile
`features/settings/data_privacy_screen.dart`.

The owner workflow has a code-defined 14-day grace period with cancellation.
It is shop-scoped. It scrubs customer profile/address data, specified CRM and
message fields, push tokens, shop/pickup identifiers, and import/export R2
objects. It revokes sessions/memberships and deletes the Supabase identity when
no other active shop membership requires it. Financial records and payout
evidence remain.

**Do not repeat the older claim that all retained records are anonymous.**
The current deletion service does not scrub order name/address snapshots,
original pasted order content, every imported database row/provider payload,
supplier contact records, or all integration credentials/configuration.
`_disable_integrations` closes provider-health breakers; its comment predates
the courier/integration storage, so it does not prove every stored connection
is removed or disabled. No backend lifecycle behavior is changed in this PR.
The public text states these limits instead of inventing a complete erasure
guarantee or a fixed retention period.

The owner should review retention and erasure coverage separately before
declaring full Google Play account-deletion compliance. Merely disclosing a
software gap does not establish a valid reason to retain every affected field.

## Publication prerequisites

- A monitored privacy/support contact is absent from tracked app/docs and from
  `SUPPORT_EMAIL` in the production secret group. Obtain it from the operator;
  never invent an address or repurpose a Git commit author's address.
- No legal company name, postal address or DPO was established. The policy
  identifies the app by its actual product name. Do not fabricate an entity.
- Play Console target-age settings are not in the repo. Ask the operator to
  confirm them. Use a business-service/not-directed-to-children statement
  without an invented numeric age threshold.
- `https://scalemyprints.com/privacy-policy` currently returns Cloudflare 403,
  “DNS points to prohibited IP”. The saved Cloudflare API credential is rejected.
  The Vercel project initially had only `ecomsbd-web.vercel.app` assigned.
  `scalemyprints.com` has now been added to that existing project, verified,
  with no redirect. Vercel still reports DNS misconfiguration. Its recommended
  apex CNAME target is `3a4bb86386d4f59a.vercel-dns-017.com` (Cloudflare supports
  apex flattening); the operator must correct the existing apex record using
  valid Cloudflare access. Do not replace unrelated API/MX/TXT records.
- A separate public account-deletion request resource is still needed. This
  policy route only publishes information and does not submit deletion requests.
  Do not paste it into Play Console as a deletion endpoint.

Google's current requirements were checked against its
[User Data policy](https://support.google.com/googleplay/android-developer/answer/10144311?hl=en)
and [account-deletion guidance](https://support.google.com/googleplay/android-developer/answer/13327111?hl=en).
Publication of a privacy URL alone does not certify all Data safety/deletion declarations.

## Web implementation and review

The routes `/privacy-policy` and `/privacy-policy/bn` are server components
outside the authenticated `(app)` layout. Both languages render complete HTML
with real anchor links and language URLs, requiring no JavaScript to read or
switch language. App UI links reuse the existing English/Bangla catalogues;
the policy's paired translations remain server-readable instead of introducing
a localization refactor. Metadata includes canonical/alternate URLs and permits
indexing on these public routes.

Links are added to the signed-out web footer, web sidebar footer, web Settings
and mobile Settings → Your data and privacy. `url_launcher`, already present
transitively, is declared directly for opening the policy in the device browser.
No business behavior is changed. The mobile link ships with the next app build.

Validation completed locally: web typecheck, lint and production build passed
(build used the existing public production Supabase/API configuration). Both
policy URLs returned 200 from the production build, logged out, at 390px and
1440px widths with JavaScript both enabled and disabled. Language navigation,
section anchors, package identity, visible date and overflow checks passed;
there were no browser page errors. The signed-out footer link passed. Focused
Flutter analysis of the changed privacy screen passed; no backend/mobile full
suite was run. The live target has not passed: it returns Cloudflare 403.
