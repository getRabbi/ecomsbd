# Runbook — Alpha SMS gateway (V4 WP7.1)

Customer SMS (today: the WP2.3 confirm link) through Alpha SMS (`sms.net.bd`).
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
5. Verify: send one confirm link to your own number (an order on a test shop whose IVR rang
   out), check the SMS arrives and the attempt row shows `SENT` with a provider reference.

## Detection
- `confirmation_attempts` (channel `SMS_LINK`) with `status` `REJECTED` / `UNKNOWN`, provider
  `alpha_sms`; app logs `alpha sms not sent` (with `provider_code`), `alpha sms answer lost`,
  `alpha sms unreachable`. Logs never carry the key, the full number or the text.
- Codes: 405 / 403 → key wrong or revoked; 410 / 411 → account expired; 413 → Sender ID not
  approved; 417 → balance empty (retried by the outbox); 421 → account not recharged yet.

## Containment
- Turn off `alpha_sms_enabled` (instant, every shop) or `enabled: false` on the account.
  Confirm links then stop being offered and rows go to seller review as before.

## Recovery
- Rotate the key: `PUT` again with new `credentials` (audited as `admin.sms_provider_updated`).
- `UNKNOWN` (no answer / unreadable answer / error 409) is **never re-sent**: the API has no
  idempotency key. The link stays answerable until it expires (6 h), then the seller decides.

## Not supported (unknown in the contract)
Delivery callbacks, segment count and price, Unicode rules, rate limits. The GSM / UCS-2
segment estimate is recorded as an estimate. Tracking SMS through messaging templates is not
built yet (see `docs/build/PROGRESS.md`, WP7.1).
