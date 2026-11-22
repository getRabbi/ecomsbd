# Runbook — Carrybee courier (V4 WP7.2)

Contract: `docs/providers/carrybee/CONTRACT.md`; manifest
`backend/app/couriers/manifests/carrybee.yaml`; code `backend/app/couriers/carrybee/`.
Flag `carrybee_enabled` (default off). Not yet verified with a live call
(`CARRYBEE_LIVE_CREATE_UNVERIFIED`).

## Turn on
1. Operator: turn on `carrybee_enabled` (global or per shop).
2. Seller: Courier Accounts → Carrybee → paste Client-ID, Client-Secret, Client-Context from
   Carrybee → Developer → API Credentials (Production, or Sandbox with the sandbox switch).
   ecomsbd checks them with `GET /api/v2/stores` and stores them encrypted (Client-Secret and
   Client-Context sealed together in the second slot).
3. Choose a pickup store (required on every Carrybee create).
4. Optional webhook: save the Carrybee webhook secret in the account **first**, then paste the
   account's callback URL into Carrybee → Webhook Integration. A verified call is answered 202
   with `X-CB-Webhook-Integration-Header` echoed, as Carrybee requires.
5. Verify (operator, one real parcel): book one order to your own address, check the
   consignment id in Carrybee, cancel it, and confirm the webhook delivery is `ACCEPTED`.

## Detection
- Booking attempts with `BOOKING_UNKNOWN` (timeout / 5xx / unreadable / no consignment id);
  logs `carrybee request refused` (HTTP status + scrubbed provider message).
- Webhook deliveries `NOT_CONFIGURED` (no secret stored) or `REJECTED` (wrong secret).

## Containment
- Turn off `carrybee_enabled`: connect and booking are refused; manual courier mode stays.

## Recovery
- `BOOKING_UNKNOWN` is never re-sent. Automatic booking recovery is Steadfast-only today, so a
  person checks Carrybee's panel for the merchant order id (Carrybee's details lookup accepts it;
  wiring it into recovery is follow-up work).
- Wrong credentials: reconnect with the right environment's three values.

## Known limits (unknown in the contract)
`transfer_status` / `payment_status` values are not published: lookups keep the raw value and
change no parcel state unless it is a documented event name. Stored webhook events are mapped
(25 events) but, as for every courier, not yet applied to parcel state. No quote, balance,
payouts, bulk (async only), reverse pickup or exchange through ecomsbd.
