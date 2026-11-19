# Voice provider manifests

One `<provider>.yaml` per voice provider, written only from that provider's
`docs/providers/voice/<provider>/CONTRACT.md`. None ships yet:
`VOICE_PROVIDER_CONTRACT_REQUIRED`.

Every capability and fact carries its own `verified_at` + `evidence`. An entry
without both reads as `unknown`, which is never usable. Prices are whole paisa.

```yaml
provider: example            # lower-case slug, also the file name
display_name: Example Voice
documentation_source: docs/providers/voice/example/CONTRACT.md
documentation_version: "2026-11"
credential_fields: [api_key, account_sid]   # secret names the adapter needs
capabilities:
  dtmf_capture:       {state: true,  verified_at: 2026-11-20, evidence: "CONTRACT.md §3 Gather"}
  dynamic_audio:      {state: false, verified_at: 2026-11-20, evidence: "CONTRACT.md §2: one file per call"}
  callback_signature: {state: true,  detail: "hmac_sha256 base64, X-Signature", verified_at: 2026-11-20, evidence: "CONTRACT.md §5"}
  caller_id:          {state: unknown}
  audio_upload:       {state: true,  verified_at: 2026-11-20, evidence: "CONTRACT.md §2: upload, then reference the file id"}
facts:
  max_call_seconds:     {value: 60,  verified_at: 2026-11-20, evidence: "CONTRACT.md §2"}
  price_per_call_paisa: {value: 150, verified_at: 2026-11-20, evidence: "price sheet 2026-11"}
blockers: []
```

Calls run only when `dtmf_capture` and `callback_signature` are verified,
`audio_upload` is known either way (`true` = the shop's intro is uploaded to the
provider first; `false` = the provider fetches our stored file by link),
`max_call_seconds` and a price are known, `credential_fields` is declared and
`blockers` is empty (`VoiceManifest.ready_for_calls`).
