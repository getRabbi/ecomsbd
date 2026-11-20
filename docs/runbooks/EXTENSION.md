# Runbook — Chrome extension (V4 Phase 5)

`apps/extension` (MV3, TypeScript, plain `tsc`). The extension is an `integration_connections` row
(provider `DEVICE`, `config.platform = "extension"`) with a capture-only ingest key (rules L2, L3).

## What it does

- Pairs with a 6-digit code from web Settings → Browser extension (`POST /v1/devices/pair`, claim
  `POST /v1/devices/pair/claim` with `platform: "extension"`). Key kept in `chrome.storage.local`.
- Content script on `business.facebook.com`, `www.messenger.com`, `web.whatsapp.com` only. It reads the
  open conversation with the verified selectors from `GET /v1/intake/rules` (`rules.extension.hosts`),
  only after the seller taps Capture; otherwise the seller's selection. Phone numbers in the open chat
  are highlighted (CSS Highlight API; nothing is sent for a highlight).
- Capture: `POST /v1/intake/captures/extension` (ingest key + `Idempotency-Key`) → an `EXTENSION` draft.
  The answer has ids and field names only. The side panel opens `<web>/quick-order?draft=<id>`, where the
  seller's session reads the draft and makes the order.
- Unpair: `POST /v1/intake/unlink` revokes the key and disconnects the link.
- Heartbeat every 6 h, rules refresh at `refresh_after_seconds` (hourly alarm checks both).

## Flags and config

- `extension_enabled` (default off) **and** `intake_device_enabled`. Off = pairing refused and every
  ingest call of a linked extension answers `FEATURE_DISABLED` (phones unaffected).
- Build-time: `ECOMSBD_API_BASE_URL`, `ECOMSBD_WEB_BASE_URL` (https origins; defaults
  `https://api.scalemyprints.com`, `https://app.ecomsbd.com`). The API origin is the only host permission.
  After an API domain move, build with the new `ECOMSBD_API_BASE_URL` and publish an update; the old
  origin keeps working while it is in `PUBLIC_BASE_URL_ALIASES` (runbook `API_DOMAIN_MIGRATION.md`).

## Detection

- Integrations Hub: the extension's health (heartbeat stale after 24 h → `NEEDS_ATTENTION`, same job as
  phones, `scan_device_health`).
- Drafts: `chat_order_drafts.capture_source = 'EXTENSION'`; capture failures show in API logs as
  `/v1/intake/captures/extension` 4xx/5xx (message text is never logged).
- Sellers report "Ei page ekhon support na…" on every capture → the page changed or no selectors are
  verified for that host (the seed ships none).

## Containment

- One shop: tenant flag row `extension_enabled = false`. Everyone: global row off.
- One browser: Connections → the extension → Disconnect (revokes its key).

## Turning page selectors on (a host's conversation read)

Selectors are an unverified provider detail until checked: the schema refuses them without
`verified_at`. For a host: open a test chat on the live page, find stable CSS selectors for the open
conversation container, each message, the message text (optional) and the chat name in the header;
check them in DevTools (`document.querySelectorAll(...)`) on two accounts. Then publish a new rule set
with them and `verified_at: <date>` (admin rules publish/activate, runbook `INTAKE_RULES.md`). Devices
pick it up on the next refresh; roll back with the previous version if captures fail.

## Recovery

- A selector stops matching → the extension falls back to the selection by itself; publish fixed
  selectors (or roll back) — no store release needed (rule L5).
- A key leaked → disconnect that extension in Connections; the seller pairs again.

## Verification (staging)

1. Turn `intake_device_enabled` and `extension_enabled` on for a test shop.
2. `cd apps/extension && npm ci && ECOMSBD_API_BASE_URL=<staging api> ECOMSBD_WEB_BASE_URL=<staging web> npm run build`;
   chrome://extensions → Developer mode → Load unpacked → `apps/extension/dist`.
3. Web Settings → Browser extension → Get a pairing code; type it in the side panel → "Linked".
4. On WhatsApp Web, select a test message (`2ta nibo 017000000xx Mirpur, Dhaka`) → Capture → "What was
   found" shows the masked phone; Open in ecomsbd → the composer opens with the draft; save.
5. Unlink in the panel → Connections shows it disconnected; a capture now asks to link again.
