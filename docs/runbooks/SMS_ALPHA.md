# Runbook — Alpha SMS gateway (V4 WP7.1)

Customer SMS (the WP2.3 confirm link and tracking SMS = order updates) through Alpha SMS (`sms.net.bd`).
Contract: `docs/providers/alpha_sms/CONTRACT.md`; manifest
`backend/app/sms_providers/manifests/alpha_sms.yaml`; code `backend/app/sms_providers/`.

## Turn on (operator)
1. Alpha SMS portal → API → Generate Key. Get a Sender ID approved there if you want one.
2. Admin console: `PUT /v1/admin/sms/providers/alpha_sms` with
   `{"credentials": {"api_key": "<key>"}, "sender_id": "<approved id or omit>", "enabled": true, "reason": "..."}`
   (needs `admin.provider_credentials_write`). The key is sealed with the credential vault;
   the console only ever shows `****` + last 4.
3. Turn on the global flag `alpha_sms_enabled` (default off). Both the flag and the account
   must be on; otherwise the deployment's `SMS_TRANSPORT` (disabled in production) is used.
4. For confirm links also: `confirmation_sms_link_enabled` + `PUBLIC_WEB_URL`.
5. For tracking SMS also: the shop turns SMS on in Customer messaging (web or app; the
   `messaging_channels` row starts off), saves an SMS order-update template, records the
   customer's SMS contact + consent, and picks SMS as the channel of the tracking step
   (recipe "Customer confirmed → book courier → send tracking" or any SEND_TEMPLATE step).
6. Verify: send one confirm link to your own number (an order on a test shop whose IVR rang
   out), check the SMS arrives and the attempt row shows `SENT` with a provider reference.

## Detection
- `confirmation_attempts` (channel `SMS_LINK`) with `status` `REJECTED` / `UNKNOWN`, provider
  `alpha_sms`; app logs `alpha sms not sent` (with `provider_code`), `alpha sms answer lost`,
  `alpha sms unreachable`. Logs never carry the key, the full number or the text.
- Codes: 405 / 403 → key wrong or revoked; 410 / 411 → account expired; 413 → Sender ID not
  approved; 417 → balance empty (retried by the outbox); 421 → account not recharged yet.

- Tracking SMS: `messaging_messages` with `channel = 'SMS'` and `status` `FAILED` / `UNKNOWN`
  / `RETRY`, `last_error` = the transport's detail (e.g. `ALPHA_SMS_NO_ANSWER`) or
  `CHANNEL_DISABLED` (gateway off after queueing). One `messaging_attempts` row per try.

## Containment
- Turn off `alpha_sms_enabled` (instant, every shop) or `enabled: false` on the account.
  Confirm links then stop being offered and rows go to seller review as before. Queued
  tracking SMS fail with `CHANNEL_DISABLED` (nothing sent); the SMS channel shows
  "not set up" and cannot be switched on. One shop: switch its SMS channel off.

## Recovery
- Rotate the key: `PUT` again with new `credentials` (audited as `admin.sms_provider_updated`).
- `UNKNOWN` (no answer / unreadable answer / error 409) is **never re-sent**: the API has no
  idempotency key. The link stays answerable until it expires (6 h), then the seller decides.
  A tracking SMS that is `UNKNOWN` (lost answer, or a worker crash while `SENDING`) is never
  re-sent either and cannot be retried from the UI; only a provable non-send (no connection,
  417) is retried by the dispatcher (≤ 5 attempts).

## Not supported (unknown in the contract)
Delivery callbacks, segment count and price, Unicode rules, rate limits. The GSM / UCS-2
segment estimate is recorded as an estimate. SMS templates are order updates only (offer /
bulk SMS needs a provider content id the page does not describe) and at most 3 parts
(estimate) per language. No SMS delivery receipts (the report endpoint is not used).
