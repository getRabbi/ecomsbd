# Voice confirmation calls (V4 Phase 3)

IVR calls that ask a customer to press 1 (confirm), 2 (cancel) or 3 (talk to the
shop). Provider-neutral: `app/voice/transport.py` (`VoiceTransport`),
`app/voice/manifest.py` + `app/voice/manifests/<provider>.yaml`, platform account
`voice_provider_connections` (`app/voice/service.py`).

**Status (WP3.1):** port, manifest loader, disabled + mock transports, platform
account and admin routes are built. No provider adapter exists:
`VOICE_PROVIDER_CONTRACT_REQUIRED`. Production runs `VOICE_TRANSPORT=disabled`,
which places nothing and refuses every callback.

**Status (WP3.2):** call orchestration is built and tested against the mock:
worker `dispatch_confirmation_calls`, callback route
`POST /v1/webhooks/voice/{provider}`, stuck-call reconciler. Calls also need the
shop's intro audio (WP3.3), so voice stays off everywhere until then.

## How a call runs (WP3.2)

1. The policy queues `CALL_QUEUED` with `next_action_at` = start + 2 min.
2. `dispatch_confirmation_calls` (seconds 14 and 44): due rows of `DRAFT`, not
   deleted orders in `ACTIVE` shops, for shops where voice is on (below), not
   in the shop's quiet hours. Per row, transaction 1 locks it
   (`FOR UPDATE SKIP LOCKED`), checks the customer has a number, has had fewer
   than 3 calls today (Dhaka day, IVR + the seller's own calls, calls that
   never rang excluded) and the breaker allows, then records an `IVR` attempt
   (`STARTED`) and `CALLING` (deadline 10 min) and **commits**. Then the
   provider is asked with `client_ref confirmation:{id}:{n}`. Transaction 2
   records the answer and provider health.
3. Answers: `PLACED` / `DUPLICATE` → wait for callbacks. `UNKNOWN` or an
   exception → attempt `UNKNOWN`, `SELLER_REVIEW` (`CALL_UNKNOWN`). `FAILED` /
   `REJECTED` / `NOT_CONFIGURED` → attempt `NOT_PLACED` / `REJECTED` /
   `NOT_CONFIGURED`, `SELLER_REVIEW` (`CALL_FAILED`). No number →
   `SELLER_REVIEW` (`VOICE_UNAVAILABLE`); 3 calls today → `SELLER_REVIEW`
   (`CALL_LIMIT`).
4. Callbacks: key 1 → `CONFIRMED` (order confirmed as the customer), 2 →
   `CANCELLED` (`CUSTOMER_DECLINED`), 3 → `SELLER_REVIEW` + a
   `CHAT_CALLBACK_REQUESTED` push to owner and managers (opens the call queue);
   another key is ignored. `NO_ANSWER`, `BUSY`, `FAILED` or `COMPLETED` without
   a key → `NO_ANSWER`; `follow_up_confirmations` calls once more after 30 min,
   then sends the SMS link (if on) or hands to the seller.
5. A `CALLING` row past its 10-minute deadline with no final callback → attempt
   `UNKNOWN`, `SELLER_REVIEW` (`CALL_UNKNOWN`). Nobody is called again.

**Voice is on for a shop** only when all hold: `voice_confirmation_enabled` for
the shop, `VOICE_TRANSPORT` available, the platform connection for that
transport `enabled`, the provider's `place_call` breaker not open, and the
shop's intro audio exists (WP3.3). Otherwise the dispatcher skips the shop and
its `CALL_QUEUED` rows stay in the seller's manual call queue
(`GET /v1/confirmations/queue` → `voice_available: false`).

## Callbacks (WP3.2)

- Only the deployment's active transport is accepted; any other provider path,
  or the disabled transport, answers `404` and stores nothing. Bodies over
  64 KiB answer `413`.
- Every accepted body is stored first in `voice_callback_deliveries` (raw body,
  SHA-256, header **names**). The same body again bumps `delivery_count`; once
  processed it is not processed again.
- Signature missing or wrong → `401`, state `REJECTED` (`SIGNATURE_INVALID`).
  Verified but unreadable → `400`, `MALFORMED`.
- Each event is deduplicated in `voice_call_events` on (provider, provider ref,
  event key = kind, or `DTMF:<digit>`). The call is found by our echoed
  `client_ref`, else the provider's reference. An event for no known call is
  kept `UNMATCHED` and applied when transaction 2 stores that reference. An
  event after the call ended or went to the seller is `LATE`: recorded on the
  attempt (key, duration, end time), never acted on.

## Switches

| Switch | Where | Default | Effect |
|---|---|---|---|
| `voice_confirmation_enabled` | feature flag (admin console, global or per shop) | off | Phase 3 calls run for the shop (WP3.2) |
| `VOICE_TRANSPORT` | env | `disabled` | `disabled` places nothing; `mock` is local/test only and refused when deployed |
| connection `enabled` | `PUT /v1/admin/voice/providers/{provider}` | off | the platform account may place calls |

A connection can be turned on only when its manifest is ready (DTMF capture and
callback signature verified with a date and evidence, max call length and a
price in whole paisa known, credential fields declared, no blockers) **and** it
has credentials **and** a callback secret. Refusals answer
`409 VOICE_PROVIDER_NOT_READY` with `details.missing`.

## Detection

- `GET /v1/admin/voice/providers` (`admin.ops_read`): manifest with evidence,
  `ready_for_calls` / `missing_for_calls`, connection (masked id only) and the
  provider's health rows.
- `GET /v1/admin/ops/provider-health`: kind `VOICE`, capability `place_call`.
  `DOWN` = 5 consecutive failed or ambiguous (`UNKNOWN`) place-call answers, and
  the breaker is open. `NEEDS_RECONNECT` = the provider refused our credentials.
- A refused number (`REJECTED` without an auth failure), `DUPLICATE` and
  `NOT_CONFIGURED` are not counted against the provider.
- WP3.2: the worker logs `confirmation calls finished` with counts per outcome
  (`PLACED`, `UNKNOWN`, `FAILED`, `CALL_LIMIT`, `quiet`, `voice_off`,
  `reconciled`, `failed`). A rising `reconciled` count means callbacks are not
  arriving: check `voice_callback_deliveries` for `REJECTED` rows (wrong
  callback secret) or none at all (callback URL not set at the provider).
- Rows stuck in `CALL_QUEUED` for a shop: `voice_available` in its queue
  response says whether voice is on; if it is off they are in the manual queue.

## Containment

1. Stop new calls for everyone: turn the connection off
   (`PUT .../{provider}` with `"enabled": false` and a reason) or set
   `voice_confirmation_enabled` off globally. Confirmations then stay in the
   manual call queue (WP2.3); nothing is lost.
2. For one shop: turn the flag off for that shop.
3. Suspected credential leak: rotate at the provider first, then `PUT` the new
   credentials and callback secret (runbook `CREDENTIAL_LEAK.md`).

## Recovery

- `NEEDS_RECONNECT`: put fresh credentials (`PUT` with `credentials`), then reset
  health with the existing provider-health repair; the breaker closes on the next
  success.
- `DOWN`: the breaker lets one call through after 2 minutes (half-open); a
  success closes it. Do not reset by hand while the provider is still failing.
- An ambiguous answer (`UNKNOWN`) is never re-called automatically (WP3.2): the
  confirmation goes to the seller.

## Verification

1. Local: `VOICE_TRANSPORT=mock`; `GET /v1/admin/voice/providers` lists `mock`
   (ready); `PUT /v1/admin/voice/providers/mock` with
   `{"credentials":{"api_key":"..."},"callback_secret":"<16+ chars>","enabled":true,"reason":"local check"}`
   answers 200 with `masked_identifier` and no secret.
2. Staging/production: `GET /v1/admin/voice/providers` is `[]` until a contract
   and its manifest ship; `PUT` answers `404 VOICE_PROVIDER_UNKNOWN`.
3. Tests: `python -m pytest tests/test_voice_transport.py tests/test_voice_calls.py -q`.
4. Staging, once a provider and WP3.3 audio exist: turn the flag on for a test
   shop, request a confirmation on an order to your own number, answer and
   press 1; the order is `CONFIRMED` and `voice_call_events` shows `DTMF:1`
   `APPLIED`. Repeat with 3: the owner gets the "customer wants to talk" push.

## Adding a provider (operator)

Put the provider's documentation in `docs/providers/voice/<provider>/` (see
`docs/providers/voice/README.md`). Then the build writes `CONTRACT.md`, the
manifest (`app/voice/manifests/<provider>.yaml`, schema in that folder's
README) and the adapter behind `VoiceTransport`. Nothing is inferred from a
community SDK or a blog post.
