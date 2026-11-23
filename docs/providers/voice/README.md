# Voice providers — what the build needs

No voice provider is wired: `VOICE_PROVIDER_CONTRACT_REQUIRED` (V4 WP3.1).
To add one, put the provider's **official** documentation (export or PDF) and
your account facts in `docs/providers/voice/<provider>/`. From it the build
writes `CONTRACT.md`, the manifest and the adapter. Anything the documents do
not say stays `unknown` and unused.

The documents must answer:

1. **Placing a call** — endpoint, auth (which secrets; how sent), request
   fields for the number (E.164?), audio, keypad capture, our reference
   (idempotency key) and the response. Does the provider retry by itself, and
   can that be turned off?
2. **Audio** — pre-uploaded file (format, size, upload API) and/or chaining
   files + spoken numbers in one call (`dynamic_audio`). Bangla TTS, if any.
3. **Keypad (DTMF)** — how the pressed digit is reported, timeouts, repeats.
4. **Callbacks** — events (ringing, answered, digit, completed, no answer,
   busy, failed), payload, retries, and **how a callback is signed** (scheme,
   header, secret). Without a verifiable signature calls cannot run.
5. **Caller id** — which number customers see; can it be chosen?
6. **Limits** — max call length, rate limits, quiet-hour rules (BTRC).
7. **Price** — per call or per minute, in BDT, with the date of the price sheet.
8. **Errors** — which answers mean "not placed" vs "may have been placed".

Account facts to add (not secrets): the account's caller id, and whether a test
number exists. Credentials go in the admin console, never in the repository.
