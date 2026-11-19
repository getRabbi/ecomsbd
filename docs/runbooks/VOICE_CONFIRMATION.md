# Voice confirmation calls (V4 Phase 3)

IVR calls that ask a customer to press 1 (confirm), 2 (cancel) or 3 (talk to the
shop). Provider-neutral: `app/voice/transport.py` (`VoiceTransport`),
`app/voice/manifest.py` + `app/voice/manifests/<provider>.yaml`, platform account
`voice_provider_connections` (`app/voice/service.py`).

**Status (WP3.1):** port, manifest loader, disabled + mock transports, platform
account and admin routes are built. No provider adapter exists:
`VOICE_PROVIDER_CONTRACT_REQUIRED`. Production runs `VOICE_TRANSPORT=disabled`,
which places nothing and refuses every callback.

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
3. Tests: `python -m pytest tests/test_voice_transport.py -q`.

## Adding a provider (operator)

Put the provider's documentation in `docs/providers/voice/<provider>/` (see
`docs/providers/voice/README.md`). Then the build writes `CONTRACT.md`, the
manifest (`app/voice/manifests/<provider>.yaml`, schema in that folder's
README) and the adapter behind `VoiceTransport`. Nothing is inferred from a
community SDK or a blog post.
