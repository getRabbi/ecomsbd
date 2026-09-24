# V3.3 Messaging & Campaigns

Built on the V2 messaging engine (`messaging_*` tables and the delivery job) and
the V3.1 Integrations Hub. There is no second message queue, contact store or
credentials model.

## Transactional vs marketing

| | Transactional (order updates) | Marketing (offers) |
|---|---|---|
| Consent | `consent` on the conversation | separate `marketing_consent`; an opt-out always wins |
| Sent by | the Messaging screen, or automation rules on order events | campaigns and flows only |
| Templates | `purpose=TRANSACTIONAL`; may use `{order_number}` | `purpose=MARKETING`; `{customer_name}`, `{shop_name}` only |
| Email extras | none | one-click unsubscribe footer + RFC 8058 `List-Unsubscribe` headers |

Automation `SEND_TEMPLATE` accepts transactional templates only. Automation rules can
still add or remove CRM tags, and a campaign can target a tag.

## Campaigns and flows

- **One-off**: a CRM audience (segment, tag, order counts, last-order window) is
  snapshotted into `messaging_campaign_recipients` at launch, or at the scheduled
  time. At most 5,000 recipients.
- **Flows** run once a day after 10:00 Asia/Dhaka and enrol at most 500 people a
  day. Each customer is enrolled once per lapse (the cycle key is their latest order):
  - `WIN_BACK`: once received a parcel, no order for N days (30–365, default 60).
  - `REPEAT_NUDGE`: latest order delivered about N days ago (7–180, default 21).
  - `INACTIVE`: the CRM "Inactive" definition (90 days).
- The scheduler (`run_campaigns`, every minute) turns up to `rate_per_minute` due
  recipients into ordinary `messaging_messages` rows (`purpose=MARKETING`).
  `dispatch_messages` then sends, retries and records them.
- Fixed anti-spam rules: nothing is queued between 21:00 and 09:00 Dhaka time. A
  frequency cap (24–720 h) applies across all campaigns and counts every queued
  message, including ones later suppressed. Consent, opt-out and bounce state are
  checked when a message is queued and again when it is sent.
- Pause holds queued messages without starting a provider attempt. Cancel skips the
  pending recipients and cancels queued messages. A campaign becomes COMPLETED only
  after every message has left the queue.
- RBAC: `settings.manage` builds, launches, resumes and cancels. `order.write` can
  pause, the emergency stop. `customer.view` can read everything. Revenue figures
  need `money.view`.

## Providers (official APIs only)

| Channel | Provider | Receipts | Gate |
|---|---|---|---|
| EMAIL | Resend (`EMAIL_TRANSPORT=provider_api`) | delivered, opened, bounced, complained, via Svix-signed webhook `POST /v1/webhooks/messaging/resend` | `EMAIL_WEBHOOK_SECRET` (`whsec_…`). Without it, statuses stop at SENT and the UI shows "not reported". |
| WHATSAPP | Meta WhatsApp Cloud API, the shop's own WABA linked in Integrations | sent, delivered, read, failed; inbound STOP opts the customer out of marketing | `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`. The WABA must be subscribed to the ecomsbd Meta app. Only templates Meta approved are sent (`POST /integrations/{id}/whatsapp/templates` syncs approval status). |
| MESSENGER | — | — | Not built. Marketing needs Meta's per-person marketing opt-in, and V3.1 keeps no Page-scoped IDs. |
| SMS | — | — | Needs an approved Bangladeshi sender. |

- WhatsApp has no idempotency key. An ambiguous send (timeout, 5xx, a crash while
  SENDING) becomes `UNKNOWN` and is never resent automatically. Rate limits and DNS
  failures before the request are retried.
- Statuses only move forward (SENT → DELIVERED → READ) and only on a verified
  callback. A bounce marks the address undeliverable. A complaint, a STOP reply or the
  unsubscribe link records a marketing opt-out (`messaging_consents.scope=MARKETING`,
  with its `source`).
- `GET /v1/messaging/unsubscribe/{token}` only shows a confirmation page, so link
  scanners do nothing. `POST` records the opt-out. Unknown tokens get the same answer.

## Analytics

Per campaign: recipients and skip reasons; message statuses; delivered and read,
only where the provider reports them (otherwise `null`); top failure codes;
opt-outs attributed to the campaign; and orders placed within the attribution
window after a message was sent. These orders are counted, not claimed as caused by
the campaign. Order value is shown only with `money.view`. `GET /v1/messaging/overview`
gives 30-day volume by purpose, channel and status.

## Privacy

Recipients are vault-encrypted and masked in every response. Account deletion
clears addresses, recipient hashes, unsubscribe tokens, rendered bodies and consent
evidence. Inbound WhatsApp text is read for an opt-out keyword and never stored.

## Migration

`a33001` (after `a32002`). Adds `messaging_campaigns` and
`messaging_campaign_recipients`, both with RLS and client-role privileges revoked.
The other changes are additive columns on `messaging_conversations`,
`messaging_consents`, `messaging_templates` and `messaging_messages`, all nullable
or server-defaulted.
