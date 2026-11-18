# Runbook: intake captures and device health (V4 WP1.3)

Every order source enters one **Intake service** (`app/intake/service.py`, rule L1)
and becomes `chat_messages` rows of one sender identity. The existing chat-order
worker (`process_chat_messages`, every 15 s) folds them into `chat_order_drafts`
as it always has. There is no second draft system.

| Source | Route | Auth | `capture_source` |
|---|---|---|---|
| Messenger / WhatsApp webhook | `/v1/webhooks/integrations/meta` | Meta signature | `META_WEBHOOK` |
| Linked phone (Autopilot uploads, Phase 4) | `POST /v1/intake/captures` | ingest key | `DEVICE_NOTIFICATION` |
| Tapped notification, share target, quick-order paste | `POST /v1/intake/captures/manual` | seller session, `order.write` | `DEVICE_NOTIFICATION` / `SHARE` / `PASTE` |
| Chrome extension (Phase 5) | `POST /v1/intake/captures` | ingest key | `EXTENSION`, refused with `FEATURE_DISABLED` until Phase 5 |

Rows that existed before WP1.3 were backfilled as `META_WEBHOOK` (migration `a41001`).

## How a capture is stored

- Device, share and paste rows use provider `DEVICE`, so no WhatsApp-only logic
  (sender number = customer) ever runs on a conversation title. `account_ref` is the
  app package (`paste` / `share` when none was given).
- Sender identity key = HMAC(`DEVICE`, package, conversation key); the conversation
  key itself is stored only encrypted.
- Message id = `h1:` + keyed hash of (identity key, sent time, text). A phone that
  re-posts the same notification, or a retried batch, adds nothing.
- `POST /v1/intake/captures`: one conversation per batch, ≤ 50 messages of
  ≤ 4,000 characters, `Idempotency-Key` (the batch id) **required**: the same key with
  the same body replays the answer, a different body is `409`. 120 uploads/min per
  device (`429` + `Retry-After`). The package must be in the shop's
  `enabled_packages`, or the answer is `403` `PACKAGE_NOT_ENABLED` with the current
  `rules_version` (the device refreshes its rules and drops the buffer).
- `POST /v1/intake/captures/manual` folds the messages at once and returns the draft
  (`draft`, or `attention_id` for a known customer's follow-up). Without a
  `connection_id` it uses the shop's hidden share/paste connection (DEVICE,
  `config.platform = "manual"`, no key, not listed in the Integrations Hub). An
  optional `Idempotency-Key` stores ids and counts only, never the draft.

## Duplicate merge

For drafts that did not come from Meta, when the selected phone first appears (or
changes):

1. another **open** draft with that phone → this draft is merged into it (its
   messages move; later messages from the same conversation follow);
2. else an **order for that phone in the last 24 h** (not cancelled) → the draft keeps
   `duplicate_of_order_id`, warning `DUPLICATE_ORDER`, and is **never announced** (no
   push, no `intake.draft_ready`). The seller decides; nothing is closed automatically.

A draft the seller was already notified about is never merged away.

## Events

`intake.draft_ready` (outbox) is written once per draft, together with the
"new order from chat" push, when it first becomes `READY_FOR_REVIEW`. Its handler
(`app/intake/jobs.py`) only records it; Phase 4's Autopilot recipe starts from it.
Payload: draft id, connection id, provider, capture source. No text, phone or name.

## Device health — `scan_device_health` (hourly, minute 47)

- **Detection.** A DEVICE connection (not the share/paste one) with no heartbeat for
  24 h (or, never having sent one, linked > 24 h ago) gets `config.health =
  "NEEDS_ATTENTION"` and `last_error_code = HEARTBEAT_STALE`. The hub shows health
  `NEEDS_ATTENTION`; the shop gets a `DEVICE_NEEDS_ATTENTION` notification (audience
  `settings.manage`) at most once per device per 24 h (`config.stale_notified_at`).
- **The connection stays `CONNECTED`**, so the phone can still authenticate; its next
  `POST /v1/intake/heartbeat` clears `health` and the error code.
- **Seller-side causes** (most common): notification access turned off, battery
  optimisation killing the app (Xiaomi, Oppo, Vivo, Realme, Samsung), app
  uninstalled, phone off. The WP1.4 settings screen shows the auto-start guide.

## Containment

| Problem | Action |
|---|---|
| A device floods captures | Revoke the connection in the Integrations Hub (revokes its key), or turn `intake_device_enabled` off for the shop. |
| Captures must stop everywhere | Turn `intake_device_enabled` off globally: ingest-key routes and manual capture answer `FEATURE_DISABLED`; devices keep their buffers. |
| An app's notifications are wrong | Seller toggles the app off (`PATCH /v1/intake/settings`), or OPS publishes a rules version without it (`INTAKE_RULES.md`). |
| A bad merge or link | The draft can be ignored; confirming still goes through the order service's duplicate check. No order is ever created by a capture. |

## Recovery and verification

- Stuck `RECEIVED` rows: the chat-order worker retries each message up to 5 times,
  then marks it `FAILED` with an exception type (never text). Check
  `SELECT capture_source, processing_status, count(*) FROM chat_messages GROUP BY 1, 2`.
- Health job: worker log line `device health scan finished` with `checked`, `stale`,
  `notified`, `failed` counts.
- Retention: `chat_order_housekeeping` (hourly) deletes messages after 30 days,
  closed drafts 30 days after closing, and DEVICE sender identities once nothing of
  theirs is left (result key `identities`).
- Verify after a change: link a test phone, upload a batch with
  `017000000xx` numbers, see one draft in `GET /v1/chat-orders`, then
  `POST /v1/intake/heartbeat` and check the hub health.
