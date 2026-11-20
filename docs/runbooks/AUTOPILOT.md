# Autopilot (V4 Phase 4)

A ready chat draft becomes an order on its own and goes to customer confirmation.
Autopilot never books: a courier is booked only after the customer confirms, by the
WP2.4 "Customer confirmed → book courier" workflow (L10, L11). Code: `app/autopilot/`,
`CONVERT_DRAFT_TO_ORDER` in `app/automation/actions.py`, routes `app/api/v1/autopilot.py`.

## How it runs

1. `intake.draft_ready` (one per draft) starts the shop's **Autopilot** workflow
   (recipe `autopilot`, always installed switched off): `CONVERT_DRAFT_TO_ORDER` →
   `REQUEST_CONFIRMATION`.
2. `CONVERT_DRAFT_TO_ORDER` checks the **shop gate** and the **order gate**, then
   creates the order through `chat_orders.confirm` (the draft id is the order's
   `client_id`: one order per draft, even on a retry). One `autopilot_decisions` row
   per draft: `CONVERTED` (with `order_id`) or `HELD` (with `reason_code`).
3. A held draft stays in Chat orders for the seller; the run's later order steps are
   `SKIPPED` / `DRAFT_NOT_CONVERTED`.

Shop gate (Settings checklist, re-checked every run), in order: flag
`autopilot_enabled` (`AUTOPILOT_DISABLED`), shop active (`SHOP_INACTIVE`), switch on
(`AUTOPILOT_OFF`), not paused (`AUTOPILOT_PAUSED`), then `NOT_IN_PLAN` (entitlement
`autopilot`, Pro), `CONFIRMATION_ENGINE_OFF`, `COURIER_NOT_READY` (default courier:
provider flag on, manifest `live_verification` present, a connected account with
credentials, no `provider_health` row DOWN / NEEDS_RECONNECT / breaker open),
`DELIVERY_RULES_INCOMPLETE` (`delivery_rules_enabled`, all three zone charges > 0,
default courier), `VOICE_UNAVAILABLE` (voice gate on, or the seller accepted
manual-queue mode), `NOT_ENOUGH_CONFIRMED` (< 20 CONFIRMED confirmations),
`CONSENT_MISSING`.

Order gate, in order: `DRAFT_NOT_READY`, `FIELDS_UNCERTAIN`, `PHONE_NOT_SINGLE`,
`ITEM_NOT_MATCHED`, `COD_NOT_CALCULATED`, `ZONE_UNRESOLVED`, `LOCATION_UNRESOLVED`
(default courier Pathao / RedX: always, until their booking sends checked location
ids), `COD_MISMATCH` (the draft's COD must equal the shop's own calculation),
`OUT_OF_STOCK`, `DUPLICATE_RECENT` (draft linked to an order, another open draft or an
order for the phone in 24 h), `POLICY_SELLER_REVIEW` (the WP2.2 policy on the same
facts), `DAILY_CAP_REACHED` (`shop_delivery_rules.autopilot_daily_cap`, Dhaka day),
then `ORDER_REFUSED` if the order service refuses (a plan limit, a changed product).

## Detection

- Home card / `GET /v1/autopilot/today`: held count and the last five reasons.
- `autopilot_decisions` grouped by `reason_code` per shop and day (ids and codes only).
- Automation runs: step `convert` outcome; a `FAILED` convert means the draft vanished
  or the run broke (look at `automation_step_runs.outcome`).
- G4 check (wrong bookings): for every `CONVERTED` decision, a consignment must exist
  only if the order's latest confirmation is `CONFIRMED`, and the order's COD and items
  must equal the confirmation snapshot (`cod_paisa_snapshot`, `order_version`).

## Containment

- Everything: turn the global flag `autopilot_enabled` off (admin console). Every run
  holds with `AUTOPILOT_DISABLED`; phones stop uploading without a tap at their next
  heartbeat (`autopilot_on: false`).
- One shop: a tenant row `autopilot_enabled = false`, or the seller turns Autopilot off
  (`PUT /v1/autopilot {"on": false}`, also clears a pause).
- "Pause 1 hour" (`POST /v1/autopilot/pause`, `order.write`): no conversion, and the
  voice gate reads `SHOP_PAUSED`, so queued IVR calls hold (they stay `CALL_QUEUED` and
  show in the seller's call queue; nothing is cancelled). `resume` needs `settings.manage`.
- Orders already made are ordinary DRAFT orders with a confirmation; cancel them in
  the orders screen if needed. Nothing was booked by Autopilot.

## Recovery

- A held draft is confirmed by the seller from Chat orders as before; its decision row
  stays `HELD` (a draft is decided once).
- After a kill switch, drafts that arrived meanwhile are not re-run (one
  `intake.draft_ready` per draft); they wait in Chat orders.

## Turning a courier live for Autopilot

After the WP0.7 smoke run on a real merchant account, add to the courier's manifest
(`backend/app/couriers/manifests/<provider>.yaml`) and deploy:

```yaml
live_verification:
  verified_at: 2026-11-20      # the smoke run's date
  evidence: "provider_smoke steadfast --allow-create, parcel <ref> cancelled"
```

Both keys are required; without them the checklist shows `NOT_LIVE_VERIFIED`.

## Verification (staging)

1. Pro shop; flags `autopilot_enabled`, `confirmation_engine_enabled`,
   `delivery_rules_enabled`, `intake_device_enabled`, `steadfast_enabled` on.
2. Delivery rules: three zone charges + default courier Steadfast; connect Steadfast;
   Steadfast manifest has `live_verification`; ≥ 20 confirmed orders.
3. Settings → Autopilot: every line ✓; tick "I will call customers myself" if voice
   is off; tick consent; Turn on. Install the Autopilot workflow in Automation and
   turn it on (plus "Customer confirmed → book courier").
4. Send a complete order chat to the linked phone (or share one): the order appears
   with its confirmation in the call queue; Home shows 1 made, 1 waiting.
5. A chat with an unknown product: Home shows "Left for you: an item was not found".
6. Pause 1 hour: a new complete chat stays in Chat orders (`AUTOPILOT_PAUSED`).
