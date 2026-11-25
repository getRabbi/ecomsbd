# Support playbook — the 10 most likely seller problems (launch, 2026-11)

Admin API: `https://api.ecomsbd.com/v1/admin/…`, header `X-Admin-Token: <token>` (never paste it in a
ticket). Phones in admin answers are masked; `admin.pii_reveal` is audited. Every flag change goes
through `POST /v1/admin/repairs/disable_provider_capability` with a reason:

```json
{"reason": "support: <ticket> <why>", "tenant_id": "<shop id or null for global>",
 "idempotency_key": "<unique>", "params": {"flag": "<flag>", "enabled": false}}
```

A tenant row overrides the global one, so switching a feature off for one shop leaves everyone else as
they are. First step for every ticket: find the shop.

```
GET /v1/admin/tenants?q=<shop name or owner email>      (slow: ~35 s, use a long timeout)
GET /v1/admin/tenants/{tenant_id}                       (masked summary)
GET /v1/admin/flags?tenant_id={tenant_id}               (what is on for this shop right now)
GET /v1/admin/ops/audit?tenant_id={tenant_id}           (who changed what)
GET /v1/admin/tenants/{tenant_id}/support-bundle        (one export for a hard case)
```

| # | Seller says | Check | Fix / flag to turn off |
|---|---|---|---|
| 1 | "Steadfast connect fails" | `GET /v1/admin/ops/provider-health` (steadfast row); bundle → courier account `last_validation_result` | Wrong key/secret → seller re-copies from the Steadfast panel. Provider outage → `refresh_provider_health` repair; if bookings fail for everyone: `steadfast_enabled` off globally (manual courier still works) |
| 2 | "Parcel status not updating" | Steadfast has no webhook: the poller runs every ~10 min. Worker logs `poll_courier_statuses` (`errors`), bundle → consignment `last_polled_at` | Wait one cycle; if `errors` > 0 for that shop → `refresh_provider_health`; credentials expired → seller reconnects |
| 3 | "Booked by mistake, cancel it" | Steadfast cancel is not in the API (manifest `cancel: false`) | Seller cancels in the Steadfast panel; the next poll brings the status back |
| 4 | "COD is wrong / delivery charge added twice" | `GET /v1/shop/delivery-rules` as the seller (or bundle): zone charges; the draft's `cod_source` (`CATALOG` / `CHAT` / `CHAT_MISMATCH`) | Seller edits the COD before saving, or fixes the zone charges. Wide problem: `delivery_rules_enabled` off for that shop |
| 5 | "Auto order detect shows nothing" | `GET /v1/admin/flags?tenant_id=` (`intake_device_enabled`); bundle → device link `listener_state` (`NO_PERMISSION` = Notification access off) and last heartbeat; `GET /v1/admin/intake-rules` (active version) | Seller turns Notification access on / picks the chat app in Settings → Auto order detect. Bad detection everywhere → activate the previous rules version (`POST /v1/admin/intake-rules/{version}/activate`); last resort `intake_device_enabled` off |
| 6 | "Too many / wrong order alerts" | Rule set version; seller's chosen apps | Seller uses "Ei sender ke ar na" / turns an app off; rule fix = a new rules version (no app release) |
| 7 | "Call queue empty / order not in it" | The order was created with **Confirm & call customer**? `GET /v1/orders/{id}/confirmation` as the seller; quiet hours 21:00–09:00 | Orders made with plain Create do not enter the queue (by design). Engine misbehaving: `confirmation_engine_enabled` off for the shop (orders stay plain orders) |
| 8 | "Customer got an SMS they did not want" / "SMS not arriving" | `GET /v1/admin/sms/providers` (account, health); shop flags `confirmation_sms_link_enabled`; messaging channel SMS on? Attempts: outcome `SENT` / `FAILED` / `UNKNOWN` | Per shop: `confirmation_sms_link_enabled` off, seller switches SMS off in Messaging → Channels. Gateway problem for all: `alpha_sms_enabled` off globally (links and SMS updates stop; nothing else changes). UNKNOWN is never re-sent automatically |
| 9 | "Invoice / label prints wrong" | Reproduce with `GET /v1/orders/{id}/documents/label?size=4x6` as the seller; Bangla needs the embedded Noto Sans Bengali (print from the browser's dialog, scale 100 %) | Printer margins → seller's print dialog. Broken template → `order_documents_enabled` off for the shop |
| 10 | "Extension won't link / capture fails" | `GET /v1/admin/flags?tenant_id=` (`extension_enabled` and `intake_device_enabled`, both needed); code expires in 10 min; claim limit per IP | New code from Settings → Browser extension; unlink + relink. Wide problem: `extension_enabled` off |

Queues to look at daily: `GET /v1/admin/ops/counts` (outbox / notification / webhook failures),
`GET /v1/admin/ops/webhooks`, `GET /v1/admin/support-cases`. Repairs list: `GET /v1/admin/repairs`
(`retry_outbox_event`, `retry_notification`, `refresh_provider_health`, `revoke_courier_credential`, …).

Escalate (operator): anything about money moving (ledger, payouts, reconciliation reversals), a
cross-shop data report, a provider-wide outage longer than an hour, or a request to switch a feature
on for one shop (SMS confirm link is per shop on request).
