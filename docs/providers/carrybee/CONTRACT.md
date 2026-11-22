# Carrybee — normalized API contract (V4 WP7.2)

**Source for every section below:** Carrybee's own developer reference,
<https://merchant.carrybee.com/webhook/documentation> (Developer → "API Documentation" in the
Carrybee merchant panel), saved by the operator from the provider's domain and read on
**2026-11-23**. The saved page (`docs/providers/carrybee/Carry Bee.html`) is not committed: it is
a signed-in merchant-panel page. No blog, plugin or package was used.

Anything this page does not state is `unknown` in the manifest and is not implemented. Values
in examples below are the page's illustrative placeholders, not real data.

## 1. Authentication and environments (source: merchant.carrybee.com/webhook/documentation, 2026-11-23)

- Three values "issued per business and per environment": **Client-ID, Client-Secret,
  Client-Context** (from the panel's "API Credentials" page). "Keep the secret server-side."
- Sent as headers on every request; `Content-Type: application/json` is required for POST/PUT:

```
Client-ID: <your-client-id>
Client-Secret: <your-client-secret>
Client-Context: <your-client-context>
Content-Type: application/json
```

| Environment | Base URL |
|---|---|
| Production | `https://developers.carrybee.com` |
| Sandbox | `https://sandbox.carrybee.com` |

- Not stated: token exchange, expiry, rotation, IP allow-list, request signing, rate limits,
  idempotency keys or duplicate protection on create.

## 2. Responses and errors (source: same page, 2026-11-23)

- Success: JSON for the resource, with `"error": false`, `"message"`, `"data"`.
- Standard error: `{"error": true, "message": "descriptive error message"}`
- Validation error (HTTP 422): `{"error": true, "message": "Validation error", "causes": {"field_name": [{"type": "validation_type", "attribute": {}}]}}`
- Status codes listed: 200 Success · 201 Created · 202 Accepted (queued / async) · 404 Not found ·
  422 Validation error · "4xx / 5xx Client / server error". No list of specific 4xx codes (e.g. for
  bad credentials) is given.

## 3. Conventions (source: same page, 2026-11-23)

| Field | Values / unit |
|---|---|
| `delivery_type` | 1 = Normal, 2 = Express |
| `product_type` | 1 = Parcel, 2 = Book, 3 = Document |
| `item_weight` | grams, 1 – 25,000 |
| `item_quantity` | 1 – 200 |
| `collectable_amount` | **Taka**, 0 – 100,000 (integer on create) |
| timestamps | ISO 8601 with timezone |

## 4. Location (source: same page, 2026-11-23)

| Method + path | Input | `data` |
|---|---|---|
| `GET /api/v2/cities` | — | `cities[] {id, name}` |
| `GET /api/v2/cities/{city_id}/zones` | `city_id` int | `zones[] {id, name, city_id}` |
| `GET /api/v2/cities/{city_id}/zones/{zone_id}/areas` | `city_id`, `zone_id` int | `areas[] {id, name, zone_id}` |
| `GET /api/v2/area-suggestion` | `search` string, ≥ 3 chars | `items[] {city_id, city_name, zone_id, zone_name, area_id, area_name}` |
| `POST /api/v2/address-details` | `query` string, ≥ 10 chars | `{city_id, zone_id}` |

## 5. Stores (source: same page, 2026-11-23)

- `POST /api/v2/stores` — create: `name` (3–30), `contact_person_name` (3–30),
  `contact_person_number` (valid phone), `contact_person_secondary_number` (optional), `address`
  (3–100), `city_id`, `zone_id`, `area_id` (int, required), `lat`, `lng` (optional). 201,
  `data: null`.
- `GET /api/v2/stores` — list: `data.stores[]` with `id` (string), `name`,
  `contact_person_name`, `contact_person_number`, `address`, `city_id`, `zone_id`, `area_id`,
  `is_active`, `is_approved`, `is_default_pickup_store`, `is_default_return_store`,
  `pending_count`. (Read-only and authenticated: usable as the credential check.)

## 6. Orders (source: same page, 2026-11-23)

### 6.1 Create — `POST /api/v2/orders` → 201

| Field | Type | Rule |
|---|---|---|
| `store_id` | string | required |
| `merchant_order_id` | string | optional, < 50 chars |
| `delivery_type` | int | required, 1 / 2 |
| `product_type` | int | required, 1 / 2 / 3 |
| `recipient_phone` | string | required, valid phone |
| `recipient_secendary_phone` | string | optional (spelled so on the page) |
| `recipient_name` | string | required, 2–99 chars |
| `recipient_address` | string | required, 10–200 chars |
| `city_id`, `zone_id`, `area_id` | int | optional |
| `special_instruction` | string | optional, < 255 |
| `product_description` | string | optional, < 255 |
| `item_weight` | int | **required**, grams 1–25000 |
| `item_quantity` | int | optional, 1–200 |
| `collectable_amount` | int | optional, Taka 0–100000 |
| `is_closed_box` | bool | optional, default false |
| `is_exchange` | bool | optional, default false |

Response: `data.order {consignment_id, store_id, merchant_order_id, collectable_amount (string),
cod_fee (number), delivery_fee (string)}`.

### 6.2 Bulk — `POST /api/v2/orders-bulk` → 202 `data: null`

Array of create bodies; "Webhooks are sent for each result"; validation errors keyed by position
(`"0.city_id"`). Results arrive only as `order.created` / `order.create-failed` webhooks.

### 6.3 Details — `GET /api/v2/orders/{consignment_id}/details`

Accepts the Carrybee consignment ID **or the merchant tracking ID** (≥ 3 chars). `data`:
`transfer_status`, `store_id`, `consignment_id`, `merchant_order_id`, `recipient_name`,
`recipient_phone`, `recipient_secondary_phone`, `recipient_address`, `collectable_amount`
(string), `collected_amount` (string), `cod_fee`, `delivery_fee` (string), `attempt`, `reason`,
`invoice_id`, `payment_status`, `updated_at` (ISO 8601). **The values of `transfer_status` and
`payment_status` are not listed** (only `"string"`).

### 6.4 Cancel — `POST /api/v2/orders/{consignment_id}/cancel` → 202

Body: `cancellation_reason` (required, < 200 chars). `{"message": "Cancellation confirmed", "data": null}`.
Which states can still be cancelled is not stated.

## 7. Reverse pickup and exchange (source: same page, 2026-11-23)

Documented, **not used by ecomsbd yet**: `POST /api/v2/orders/reverse-pickup` (standalone return
shipment), `POST /api/v2/orders/{consignment_id}/reverse-pickup` (return from a delivered order),
`POST /api/v2/orders/{consignment_id}/exchange`. Their fields are on the source page.

## 8. Webhooks (source: same page, 2026-11-23)

- Registered on the panel's "Webhook Integration" page; Carrybee POSTs every status change.
- "Carrybee includes a secret in the **`X-CB-Webhook-Integration-Header`** header so you can
  verify requests." A static shared secret (no HMAC or timestamp is described).
- The endpoint must: resolve within 3 redirections; have a valid TLS certificate and answer
  within the timeout (value not stated); **return 202 for the integration event**; **echo back
  `X-CB-Webhook-Integration-Header` with the exact secret value**. The integration event's body
  is not shown.
- Not stated: retries, ordering, an event id, delivery timeout.

Every event carries `event`, `store_id`, `merchant_order_id`, `timestamptz` (ISO 8601) and,
except `order.create-failed`, `consignment_id`. Extra fields per event:

| Event | Extra fields |
|---|---|
| `order.created`, `order.updated` | `collectable_amount` (number), `cod_fee`, `delivery_fee` (number, e.g. 83.46) |
| `order.create-failed` | `position`, `reason`, `causes` (no `consignment_id`) |
| `order.pickup-requested`, `order.assigned-for-pickup`, `order.picked`, `order.pickup-failed`, `order.pickup-cancelled` | — |
| `order.at-the-sorting-hub`, `order.on-the-way-to-central-warehouse`, `order.at-central-warehouse`, `order.in-transit`, `order.received-at-last-mile-hub` | — |
| `order.assigned-for-delivery` | `attempt`, `agent_name`, `agent_phone` |
| `order.delivery-on-hold` | `attempt`, `reason`, `remarks` |
| `order.delivered` | `collected_amount`, `attempt`, `remarks` |
| `order.partial-delivery` | `collected_amount`, `attempt`, `reason`, `remarks` |
| `order.delivery-failed` | `attempt`, `reason`, `remarks` |
| `order.returned` | `reason`, `remarks` |
| `order.paid-return` | `collected_amount`, `attempt`, `reason`, `remarks` |
| `order.exchange` | `collected_amount`, `reason`, `remarks` |
| `order.paid` | `invoice_id` |
| `order.returned-at-sorting`, `order.returned-in-transit`, `order.returned-to-merchant` | — |

(`agent_phone` is a rider's number: never logged.)

## 9. Not in the page (unknown → not implemented)

Charge quote / price plan, balance, payouts / invoice detail endpoint, customer delivery stats,
the `transfer_status` / `payment_status` value lists, credential-error status codes, rate limits,
webhook retries, event ids, idempotency on create.

## 10. ecomsbd reading (decisions, not provider facts)

- Create timeouts, dropped connections, 5xx and unreadable bodies are **UNKNOWN** (the parcel may
  exist; no idempotency key) and are never created again. §6.3 accepts `merchant_order_id`, but
  ecomsbd's automatic recovery is Steadfast-only, so a person resolves them for now.
- Status comes from webhooks (§8, enumerated). A §6.3 lookup keeps `transfer_status` verbatim
  with no normalized status until its values are documented or observed.
- Webhook check: constant-time compare of `X-CB-Webhook-Integration-Header` with the shop's
  stored secret; no secret stored → refuse everything; a verified request gets 202 and the
  header echoed.
- `collectable_amount` is whole Taka: a COD with a paisa remainder is split as in the existing
  `BookingPreview.cod_residual_paisa`.
