# Runbook — Official Meta path on (V4 Phase 6)

Messenger (a Facebook Page) and WhatsApp (a Cloud API number) connections already deliver chats
through the Meta webhook into the Intake service and `chat_order_drafts` (rule L1). Phase 6 makes
that path the primary one for a shop that has it, and opens Messenger / WhatsApp in the apps.

## What it does

- **Coverage.** While a shop's `MESSENGER` connection delivers (state `CONNECTED`, webhook not
  `FAILING`), Meta Business Suite (`com.facebook.pages.app`) is in the shop's
  `shop_intake_settings.covered_packages`; a delivering `WHATSAPP` connection covers WhatsApp Business
  (`com.whatsapp.w4b`). Linked phones drop covered apps from `enabled_packages` on their next rules
  refresh (`GET /v1/intake/rules`, the ETag changes). Personal Messenger (`com.facebook.orca`) and
  personal WhatsApp (`com.whatsapp`) are never covered: the webhook never sees those chats.
- Coverage is recomputed (`app/intake/coverage.py::refresh_coverage`) on connect / reconnect
  (`went_live`), disconnect, pause / resume (`PATCH /v1/integrations/{id}`), every health check
  (`POST /v1/integrations/{id}/test` and the 5-minute maintenance), and hourly for every shop with a
  Meta connection in `scan_device_health` (catches a token found expired while a webhook was processed
  or a message sent, a failing webhook subscription, the flag switched off).
- **Launch gate.** `GET /v1/integrations` → `providers[].coming_soon`. For `MESSENGER` / `WHATSAPP` it is
  `false` only when all hold: `META_APP_REVIEW_APPROVED=true`; the Meta app is set up (no blocker; for
  WhatsApp the Embedded Signup is configured and public); the shop's `meta_official_path_enabled` flag;
  and a passing health check — some `CONNECTED` connection of that provider (any shop) with a webhook not
  `FAILING` succeeded (health check or processed webhook) within 24 h with no error after it. Web
  (`isComingSoonFor`) and the phone app (`isLaunchGated`) show Coming soon for any other answer,
  including none. Shopify, WooCommerce and Custom website stay Coming soon. Messaging / campaign
  WhatsApp sending stays Coming soon (outbound, not this path).
- No new table, job or webhook. Drafts from the webhook run through the same confirmation + Autopilot
  pipeline (`intake.draft_ready` → the Autopilot recipe), tested end to end in
  `backend/tests/test_meta_official_path.py`.

## Flags and config

- `meta_official_path_enabled` (default off; per shop or global). Off = nothing covered (the hourly sweep
  clears coverage) and Messenger / WhatsApp stay Coming soon.
- `META_APP_REVIEW_APPROVED` (default `false`): the operator's attestation after Meta's approval email.
- Existing: `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` (webhook refuses everything
  without the secret), WhatsApp Embedded Signup settings (`docs/WHATSAPP_EMBEDDED_SIGNUP.md`).

## Detection

- Logs: `intake coverage changed` (tenant id, covered apps before / after; no customer data);
  `device health scan finished` carries `coverage_shops` / `coverage_failed`; `intake coverage sweep
  failed` names the tenant and the error type.
- Integrations Hub health of the shop's Meta connection (`AUTH_EXPIRED`, `WEBHOOK_FAILING`).
- A seller reporting missed orders from Business Suite / WhatsApp Business: check the connection is
  `CONNECTED` with recent `last_webhook_at`; if not, coverage should already be empty — check
  `shop_intake_settings.covered_packages` for the tenant.
- Duplicate drafts (phone + webhook for the same chat) mean coverage is missing: the shop's flag is off,
  or the phone has not refreshed rules yet (`refresh_after_seconds`).

## Containment

- Stop coverage and close the gate for everyone: switch `meta_official_path_enabled` off globally (admin
  console). The next hourly sweep (or any connection change) clears `covered_packages`; phones read the
  apps again on their next rules refresh. For one shop: a tenant row with `enabled=false`.
- Close the gate only (keep coverage): set `META_APP_REVIEW_APPROVED=false` and restart the API.
- Meta itself broken (outage, app restricted): `PROVIDER_OUTAGE.md`; coverage follows connection health.

## Recovery

- Coverage wrong for one shop: run the sweep — `scan_device_health` (worker, minute 47) or, from a
  shell with the backend env, `python -c "import asyncio; from app.intake.coverage import
  sweep_coverage; print(asyncio.run(sweep_coverage()))"`. It recomputes from the connections; it never
  edits orders, drafts, stock or money.
- A Page / number reconnected after expiry: the reconnect (`went_live`) covers again at once.

## Verification (staging, then production)

1. Before approval: set nothing; `GET /v1/integrations` shows `coming_soon: true` for both.
2. Meta approves the app (Messenger `pages_messaging`, WhatsApp Business Management / Messaging): set
   `META_APP_REVIEW_APPROVED=true`, restart. Still Coming soon (flag off).
3. Turn `meta_official_path_enabled` on for the operator's test shop only. Bootstrap the first passing
   check (nothing is connected anywhere yet, so the gate stays shut): with the test shop owner's
   session, `POST /v1/integrations {"provider": "MESSENGER", "name": "Test Page"}` → open the returned
   `authorize_url`, pick the Page (`POST /v1/integrations/{id}/messenger/page`), then
   `POST /v1/integrations/{id}/test` → `ok: true`. For WhatsApp use the Embedded Signup flow
   (`docs/WHATSAPP_EMBEDDED_SIGNUP.md`) the same way.
4. Reload the hub in the web and the app: Messenger (and WhatsApp, once its check passes) no longer
   shows Coming soon for the test shop.
5. On the test shop's linked Android phone: Settings → Auto order detect → sync; Meta Business Suite /
   WhatsApp Business show as covered by the official connection. Send a Page message with an order
   ("2ta nibo 017000000xx Mirpur, Dhaka"): one draft arrives with source Meta webhook, none from the phone.
6. Disconnect the Page: after the phone's next rules refresh, Business Suite is read again.
7. Turn the flag on globally (or a rollout percentage) only after 3–6 pass.
