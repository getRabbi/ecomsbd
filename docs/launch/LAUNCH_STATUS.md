# Launch status — what is live (written 2026-11-25, launch run 12)

Production: API `https://api.ecomsbd.com` (alias `https://api.scalemyprints.com`), web
`https://app.ecomsbd.com` + `https://ecomsbd.com`, database migration `a48001`. Free Launch Mode:
every feature free, billing off. Exact flag changes, times and rollbacks: `docs/build/PROGRESS.md`
→ "Launch log".

## Live for every shop

| Feature | Flag (scope) | Since | What the seller does |
|---|---|---|---|
| Steadfast, Pathao, RedX booking + status + COD | `steadfast_enabled`, `pathao_enabled`, `redx_enabled` (global) | 2026-10-01 (V3) | connects their own courier account |
| First-party usage milestones (funnel) | `product_events_enabled` (global) | 2026-11-25 01:00 | nothing (no customer data) |
| Delivery charges by zone + default courier | `delivery_rules_enabled` (global) | 2026-11-25 01:32 | Settings → Delivery rules; nothing changes until saved |
| Invoice + 4×6 label print (Bangla, barcode) | `order_documents_enabled` (global) | 2026-11-25 02:03 | Print invoice / Print label |
| SMS order updates through Alpha SMS | `alpha_sms_enabled` (global platform switch) | 2026-11-25 01:48 | turns SMS on in Messaging → Channels (default off) |

## Checked on the test shop, not yet widened (one admin call each when the operator says go)

| Feature | Flag | State |
|---|---|---|
| Paste / share / Auto order detect into drafts | `intake_device_enabled` | test shop only. Server check passed (L2.7); the 10-minute global watch was stopped before it ran (machine asleep + low memory). Safe to widen: seller opt-in (link a phone + Notification access); privacy policy covers it |
| Browser extension pairing + capture | `extension_enabled` (needs intake) | test shop only, same reason. Sellers cannot install it until the Chrome Web Store approves it |
| Confirmation call queue | `confirmation_engine_enabled` | test shop only, same reason. Creates no customer contact by itself (Launch log L3.1e) |

Widening = `POST /v1/admin/repairs/disable_provider_capability` `{"params": {"flag": "<flag>", "enabled": true}, "tenant_id": null, ...}`, then 10 minutes of health / 5xx / worker / pool checks; in that order: intake → extension → confirmation engine.

**Copy ahead of the product until then:** the public site (live since 01:49) and the Play / marketing texts already name the confirmation call queue and paste-to-draft. Widen `confirmation_engine_enabled` and `intake_device_enabled` before promoting them, or the claim is true only for the test shop.

## Seller opt-in, switched on per shop on request

| Feature | Flag | Why not global |
|---|---|---|
| SMS confirm link (after an unanswered call the customer gets a link to Confirm / Cancel) | `confirmation_sms_link_enabled` (tenant rows only; on for the test shop) | No per-shop switch in the app: with the flag on, marking "Dhoreni" sends an SMS to the customer by itself (`app/confirmations/links.py` `after_seller_no_answer`), and every SMS is paid from the platform's Alpha SMS balance. Turn it on for a shop that asks. |

## Not live, and why

| Feature | Why | Next step |
|---|---|---|
| Autopilot | no courier has `live_verification` yet; G4 pilot | first real Steadfast parcel with a poller status (ledger WATCH item) |
| Voice confirmation calls | no voice provider contract | provider docs (`docs/providers/voice/`) |
| AI order parser | holdout has 8 messages (< 30 needed) | add 30–50 masked real messages, run the live eval |
| Meta official path (Messenger / WhatsApp API) | Meta App Review | G6 |
| BD Courier risk lookup | BD Courier's written OK + policy text | email support@bdcourier.com |
| Carrybee | no live credentials checked | sandbox → production check (runbook `COURIER_CARRYBEE.md`) |
| Manual payments, referrals | billing is off in Free Launch Mode | after Free Launch |
| Shopify / WooCommerce / website | launch gate | later |
| Reconciliation auto-match | shadow mode only; no real statements yet | real payout statements |
| iPhone app | skipped for now (operator) | — |

## Known limits (tell sellers)

- **Steadfast has no webhook:** parcel status is polled about every 10 minutes.
- **Steadfast bookings cannot be cancelled through the API:** cancel in the Steadfast panel; the next poll brings it back.
- **Saving an order never books a courier;** booking is always the seller's tap.
- **Zone delivery charges** are added only to orders made from a chat message (paste, share, phone, extension); a COD the customer stated in chat is never replaced (a mismatch is flagged).
- **Quiet hours** 21:00–09:00 (per shop, Settings → Delivery rules): no confirmation SMS is sent, and "call now" asks for an override (audited).
- **SMS cost:** Alpha SMS returns no price per message; segments are estimated and recorded, money is the portal balance.
- **Auto order detect** needs the Android 3.1 app (closed testing first) and reads only the chat apps the seller turns on; nothing leaves the phone before the seller taps Create.
- **Chrome extension** works only after Chrome Web Store approval (or unpacked for testing).
- **Email** (Resend) is not configured: no business emails are sent. **Backend Sentry** is off (no DSN); the Android build sends crash reports only if built with a DSN.
