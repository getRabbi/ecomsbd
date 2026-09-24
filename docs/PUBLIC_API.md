# ecomsbd Public API v1

Base URL: `https://<your API host>/public/v1`. Integrations → Custom Website and the Developer
portal both show it. All bodies are JSON, and all money is in **paisa** (integer; ৳1 = 100).

## Authentication and scoped keys

Owners create keys in **Developer → API keys** (or through Integrations → Custom Website).
The full key (`ec_live_<id>.<secret>`) is shown **once** and stored only as a keyed hash.

```
Authorization: Bearer ec_live_…
```

| Scope | Allows |
| --- | --- |
| `orders:read` / `orders:write` | list/read orders; create orders, confirm or cancel |
| `customers:read` / `customers:write` | list customers; create a customer |
| `products:read` / `products:write` | list products and look up by SKU; create a product |
| `inventory:read` / `inventory:write` | read stock; record a stock adjustment |
| `sources:write` | send orders into an order source (Custom Website) |

- A key can have an optional expiry. An expired or revoked key gets `401`.
- **Rotate** issues a new secret with the same scopes and expiry and stops the old one at once.
- `GET /me` needs no scope. It returns `key_id`, `shop_id`, `scopes`, `rate_limit_per_minute`
  and `expires_at`, so use it to test a key.

## Idempotency

Every `POST` requires an `Idempotency-Key` header of 8–120 characters.

- Same key and same body: you get the stored response again, and nothing is created twice.
- Same key with a different body: `409 IDEMPOTENCY_KEY_CONFLICT`.
- `POST /sources/{source_id}/orders` also dedupes on `external_order_id`, so a website
  can use its own order number for both.

## Errors

Every error body has the same shape:

```json
{ "code": "VALIDATION_ERROR", "message_en": "…", "message_bn": "…", "retryable": false,
  "reference_id": "…", "details": { } }
```

| HTTP | Typical `code` |
| --- | --- |
| 401 | `UNAUTHENTICATED`: missing, revoked or expired key |
| 403 | `FORBIDDEN`: the key lacks the scope (`details.scope`) |
| 404 | `NOT_FOUND`: not in this shop |
| 409 | `IDEMPOTENCY_KEY_CONFLICT`, or `CANCELLED_AFTER_BOOKING` (see order status) |
| 422 | `VALIDATION_ERROR` (`details` lists fields) |
| 429 | `RATE_LIMITED`: wait `Retry-After` seconds |

## Rate limits

Each key allows its own requests per minute (1–600, set on the key, default 60). The whole
shop allows 1000 per minute. Over the limit you get `429` with `Retry-After`.

## Pagination

List endpoints take `limit` (1–100, default 30) and `offset`, and return
`{ "items": [...], "next_offset": n }`. You are at the end when `items` has fewer than
`limit` entries.

## Orders

| Method | Path | Scope |
| --- | --- | --- |
| GET | `/orders?limit&offset` | `orders:read` |
| GET | `/orders/{order_id}` (includes `items`) | `orders:read` |
| POST | `/orders` | `orders:write` |
| POST | `/sources/{source_id}/orders` | `sources:write` + `orders:write` |
| POST | `/orders/{order_id}/status` | `orders:write` |

An order has `id`, `order_number`, `customer_id`, `customer_name`, `customer_phone_masked`,
`status`, `channel`, `cod_amount_paisa`, `created_at` and `updated_at`. `status` is one of
`DRAFT`, `CONFIRMED`, `PACKED`, `FULFILLMENT_STARTED`, `COMPLETED` or `CANCELLED`.

Order body, used by `POST /orders` directly and as `payload` for a source order:

```json
{
  "phone": "01712345678",
  "customer_name": "Rahim", "address": "House 1, Road 2", "district": "Dhaka", "area": "Mirpur",
  "items": [
    { "name": "Black Abaya XL", "quantity": 1, "unit_price_paisa": 125000 },
    { "product_id": "…", "variant_id": "…", "quantity": 2 }
  ],
  "cod_amount_paisa": 125000, "discount_paisa": 0, "delivery_fee_paisa": 0, "note": "Call first"
}
```

Source order: `{ "external_order_id": "WEB-1001", "payload": { …order body… } }`.

Order status: `{ "status": "CONFIRMED" | "CANCELLED", "reason": "…" }`. The response carries
`result`:

- `UPDATED` or `UNCHANGED`;
- `CONFLICT` with HTTP `409` and `code: CANCELLED_AFTER_BOOKING` when the parcel is already
  booked. The order is **not** cancelled; the seller gets a sync conflict to resolve.

Courier progress is delivered by webhook (`tracking.assigned`, `order.delivered`,
`order.returned`, `consignment.status_changed`). Courier accounts and courier APIs are not
part of the public API.

## Customers

| Method | Path | Scope |
| --- | --- | --- |
| GET | `/customers?limit&offset` | `customers:read` |
| POST | `/customers` `{ "phone", "name", "address" }` | `customers:write` |

A customer has `id`, `name`, `phone_masked`, `created_at` and `updated_at`. Full phone numbers
are never returned.

## Products and variants

| Method | Path | Scope |
| --- | --- | --- |
| GET | `/products?limit&offset&sku=` | `products:read` |
| POST | `/products` | `products:write` (+ `inventory:write` when `opening_stock` > 0) |

`sku` matches a product's own SKU or one of its variants', and the result then includes
`variants` (`id`, `name`, `sku`, `stock_on_hand`, `is_active`). A product has `id`, `name`,
`sku`, `default_selling_price_paisa`, `cost_paisa`, `stock_tracking_enabled`, `stock_on_hand`,
`is_active`, `created_at` and `updated_at`.

## Inventory

| Method | Path | Scope |
| --- | --- | --- |
| GET | `/inventory/{product_id}` (with `variants`) | `inventory:read` |
| POST | `/inventory/{product_id}/adjustments` | `inventory:write` |

Adjustment: `{ "quantity_delta": -2, "reason": "MANUAL_ADJUSTMENT" | "OPENING" |
"DAMAGED_WRITE_OFF", "variant_id": "…", "note": "…" }`. Each one is a ledger movement;
stock is never overwritten.

## Signed webhooks

Create an endpoint in **Developer → Webhooks**. It must be public HTTPS on port 443. The
signing secret is shown once, and **Rotate secret** replaces it at once.

Each delivery is a `POST` with a JSON body:

```json
{ "id": "<event id>", "type": "order.delivered", "version": "1",
  "created_at": "…", "shop_id": "…", "data": { "order_id": "…", "new_status": "DELIVERED" } }
```

The headers are `X-Ecomsbd-Signature: t=<unix>,v1=<hex>`, `X-Ecomsbd-Event-Id` and
`X-Ecomsbd-Delivery-Id`.

To verify a delivery:

1. Compute `hex(HMAC-SHA256(secret, "<t>." + raw_body))` and compare it in constant time.
2. Reject a `t` more than **300 s** from your clock. This stops replays.
3. Deduplicate on `X-Ecomsbd-Event-Id`: delivery is at-least-once.
4. Return `2xx` only after you have stored the event.

A delivery that does not get `2xx` is retried with backoff up to 6 attempts and then marked
`FAILED`; you can retry it from the portal.

Topics: `order.created`, `order.updated`, `order.status_changed`, `order.confirmed`,
`order.cancelled`, `order.booked`, `order.fulfilled`, `tracking.assigned`, `order.delivered`,
`order.returned`, `consignment.status_changed`, `inventory.updated`, `product.updated`,
`import.committed`, `automation.workflow`, and `webhook.test` (portal test only).

`data` holds identifiers and states only: `order_id`, `consignment_id`, `import_id`,
`status`, `old_status`, `new_status`, `from`, `to`, `changed`, `provider`, `tracking_code`,
`product_id`, `variant_id`, `sku`, `stock_on_hand` and `reason`. Customer data and money are
never included. Fetch the order with your key when you need more.

## SDKs and examples

`sdk/` has thin, dependency-free helpers: `js/ecomsbd.mjs` (+ `.d.ts`), `php/Ecomsbd.php`
and `python/ecomsbd.py`. It also has Next.js and Laravel examples. Their routes and webhook
signing are checked against this API in CI.
