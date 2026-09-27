# Free launch and paid re-enablement

The backend is the sole source of launch policy. No migration or subscription
update is needed. The existing stored plan codes (`free`, `starter`, `pro`),
catalogue, prices, overrides, subscription lifecycle, billing tables, 402 errors,
provider switches and paywall components remain intact.

## Current production configuration

Set these in the existing Northflank `ecomsbd-production` secret group, shared by
`ecomsbd-api` and `ecomsbd-worker`, then restart/redeploy both processes:

```dotenv
FREE_LAUNCH_MODE=true
BILLING_ENABLED=false
RISK_CHECKS_DAILY_SAFETY_LIMIT=500
```

Source defaults are `false` for both switches. The production environment
template explicitly opts into launch mode. Do not put either switch in Flutter
build defines or `NEXT_PUBLIC_*` variables.

`GET /v1/billing/entitlements` keeps the resolved real `plan`, subscription status,
source and expiry, and adds `free_launch_mode`, `billing_enabled` and
`effective_access` (`full_access` or `plan`). The `entitlements` map carries the
effective capabilities. `/billing/subscription` still returns the real record.
No shop is assigned a fake paid subscription. The display hides billing dates
and payment notices during launch and says “Full access — Free launch” in EN/BN.

Flutter and web read that authenticated response, refresh it every minute while
observed, and hide plan UI while loading. Flutter also refreshes on tenant change;
web refreshes on focus and resets on account change. Existing plan URLs show
the localized launch notice. New clients are required to hide old purchase UI;
the backend grants launch access to older clients too, but cannot change their
compiled navigation.

## Enforcement and exceptions

`EntitlementService.plan_for()` creates an in-memory capability override. The
underlying resolver and subscription rows are unchanged. Boolean capabilities
are granted and monetization limits become unlimited. All callers, including
imports, exports, team invitations and background delivery, use the same service.
Usage is still counted: returning to paid mode retains spend already recorded
in the current period.

Unlocked: orders, courier-account and team-seat allowances, bulk booking,
reconciliation, profit history, advanced analytics, CSV export, web dashboard,
auto-risk, SMS and AI-parse entitlement allowances. The already released
integrations, workflows/automation, campaigns, forecasts, procurement, developer
platform and risk/network UI remain available according to RBAC.

Risk checks have a **safety** purpose: preventing phone-number probing. During
launch the cap is independent of plan (500/day by default, matching the existing
highest-tier ceiling), is counted atomically, and returns 429 when exhausted.
It never offers an upgrade. Other rate limits, campaign caps, provider quotas,
network aggregation thresholds, consent, credentials, permissions, tenant guards
and destructive confirmations are untouched. Full access does not activate
Shopify/Meta/WhatsApp, RedX, WooCommerce, Resend or a licensed external-risk
provider without their real setup. AI parsing still needs a configured adapter.

`BILLING_ENABLED` is an additional master purchase switch. Launch mode always
wins: `/billing/channel` offers no providers or external-payment links, and
new web checkout is refused both at the API and service boundary. Verification,
restoration, signed webhooks, cancellation and reconciliation of existing paid
purchases are preserved, so disabling new sales does not strand prior payments.

## Re-enable paid plans

1. Set `FREE_LAUNCH_MODE=false` in the same shared secret group and roll/restart
   **both** API and worker. This restores the original resolver, plan limits and
   402 responses; clients restore their retained plan navigation and paywalls
   after fetching the new state. No client rebuild or database rewrite is needed.
2. Leave `BILLING_ENABLED=false` until a real billing provider and client purchase
   flow are ready. This enforces plans without pretending checkout is available.
3. When ready, set `BILLING_ENABLED=true`, configure the provider credentials and
   enable its existing `play_billing_enabled` or `bkash_web_billing_enabled`
   feature flag. Keep the distribution-channel policy. Both switches alone do
   not substitute for provider implementation/approval. The web dashboard does
   not yet implement checkout; its retained API architecture remains available
   for that future work.
4. Verify a Free shop gets 402 for advanced analytics, a real paid shop receives
   its stored benefits, the channel offers only configured permitted providers,
   and both clients match the server. Reversal to launch is the first two values
   above; it requires no data rollback.

## Verification

Focused backend tests: `pytest -q tests/test_free_launch.py tests/test_entitlements.py
tests/test_billing.py`. Additional coverage includes security, tenant isolation,
team, analytics, integrations and delivery tests. Flutter tests cover EN/BN old
plan routes, hidden navigation/paywalls, and paid-mode restoration alongside the
existing billing tests. Web unit tests cover display policy; verify the settings
and stale routes in a browser against the same entitlement response.

Post-deploy: check `/health/ready`, API and worker deployed SHAs and shared flag
values, authenticated `/billing/entitlements` (`full_access`), `/billing/channel`
(`can_purchase=false`, no providers), advanced analytics (200), unauthenticated
analytics (401), and the preserved external-provider blockers. Never print bearer
tokens or provider credentials in release evidence.
