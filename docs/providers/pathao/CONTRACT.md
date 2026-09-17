# Pathao Courier — normalized API contract

**Source (first-party):** `github.com/pathao-eng/courier-woocommerce-plugin`, the
WooCommerce plugin published and maintained by Pathao's engineering
organisation. Read on 2026-09-17, `main` branch.

| File | What it evidences |
| --- | --- |
| `pathao-bridge.php` | Base URLs, login, all outbound endpoints, create payload |
| `plugin-api.php` | Webhook route, signature check, event→status map, response header |
| `wc-order-list.php` | `delivery_type` and `item_type` wire codes |
| `js/ptc-location-manager.js` | Store, city, zone and area field names |

This is code Pathao ships to merchants, so every path and field name in it is
one Pathao itself calls in production. That is the standard this repository
requires before an endpoint may be called against a live merchant account.

> **Why the source matters more than usual here.** The widely-repeated community
> contract says Pathao authenticates at `POST /aladdin/api/v1/issue-token` with
> `grant_type`, `username` and `password`. Pathao's own current plugin
> authenticates at `POST /aladdin/api/v1/external/login` with `client_id` and
> `client_secret` alone. Implementing from memory would have asked sellers for
> their Pathao account password and then failed.

Anything marked **UNVERIFIED** below is a fact about the source: Pathao's plugin
does not read it, so ecomsbd does not claim it.

---

## 1. Hosts

| Environment | Base URL |
| --- | --- |
| Live | `https://api-hermes.pathao.com` |
| Sandbox | `https://courier-api-sandbox.pathao.com` |

Selected per courier account in ecomsbd, not per deployment, so a seller can
rehearse against sandbox while the rest of the shop is live.

## 2. Authentication

```
POST {base}/aladdin/api/v1/external/login
Content-Type: application/json
Accept: application/json

{"client_id": "...", "client_secret": "..."}
```

Response: `{access_token, refresh_token, expires_in, token_type}`.

Every other call carries:

```
Authorization: Bearer {access_token}
Content-Type: application/json
Accept: application/json
source: ecomsbd
```

Pathao's plugin re-calls the **same login endpoint** when a token expires rather
than exercising `refresh_token`, so ecomsbd does the same. `expires_in` is
treated as seconds from the call; ecomsbd refreshes 120 s early and falls back to
a 30-minute lifetime when Pathao omits it. **TOKEN_LIFETIME: UNVERIFIED.**

A non-2xx login returns a body with a `message` field. No other error field is
documented — **ERROR_BODY_CONTRACT: UNVERIFIED**.

## 3. Endpoints called

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/aladdin/api/v1/external/login` | Issue access token |
| GET | `/aladdin/api/v1/user/short-info` | Credential check (safest read) |
| GET | `/aladdin/api/v1/stores` | Pickup stores |
| GET | `/aladdin/api/v1/countries/1/city-list` | Cities |
| GET | `/aladdin/api/v1/cities/{city_id}/zone-list` | Zones in a city |
| GET | `/aladdin/api/v1/zones/{zone_id}/area-list` | Areas in a zone |
| POST | `/aladdin/api/v1/orders` | Create one parcel (201) |
| POST | `/aladdin/api/v1/orders/bulk` | Create a batch, body `{orders: [...]}` |

Endpoints that **do not exist** in Pathao's published integration: status
lookup / order info, price quote, cancel, return request, balance, payouts,
payment listing, customer statistics. Their absence is reported to the seller
as an unavailable capability with a reason, never worked around.

## 4. Create order payload

Built by `makeDto()` in `pathao-bridge.php`, in this order:

| Field | Type | Always sent | Notes |
| --- | --- | --- | --- |
| `store_id` | int | yes | Mandatory; no default exists |
| `merchant_order_id` | string | yes | Our reference; the key to an ambiguous create |
| `recipient_name` | string | yes | |
| `recipient_phone` | string | yes | 11-digit national form, e.g. `01712345678` |
| `recipient_secondary_phone` | string | yes | Empty string when absent |
| `recipient_address` | string | yes | Free text; 10–220 characters |
| `delivery_type` | int | yes | `48` Normal, `12` On Demand |
| `item_type` | int | yes | `2` Parcel, `1` Document |
| `special_instruction` | string | yes | |
| `item_quantity` | int | yes | |
| `item_weight` | float | yes | Kilograms |
| `item_description` | string | yes | |
| `recipient_city` | int | **only if non-empty** | Fallback path |
| `recipient_zone` | int | **only if non-empty** | Fallback path |
| `recipient_area` | int | **only if non-empty** | Fallback path |
| `amount_to_collect` | int | if not `""` | Taka, cast to integer |

The three location ids being *omitted rather than sent empty* is the evidence
that a sufficiently complete free-text address is a supported primary path.
Structured area selection is the fallback.

Success response is read as `data.consignment_id` and `data.delivery_fee`. No
tracking code is returned separately. **Failure body shape: UNVERIFIED** — the
plugin decodes it and passes it through without reading a field.

`/orders/bulk` sends `{orders: [...]}` and the plugin **never reads the response
body**. **BULK_RESPONSE_SCHEMA: UNVERIFIED.**

## 5. Webhook

**Inbound to ecomsbd.** The merchant sets the callback URL in the Pathao
merchant panel and configures a webhook secret there.

- Request header `X-PATHAO-Signature` carries that secret **verbatim**. Pathao's
  plugin compares it for equality — it is a shared secret, **not an HMAC over
  the body**.
- Body fields read: `event`, `merchant_order_id`, `order_status`,
  `delivery_fee`, `consignment_id`.
- `order_status` may be absent, in which case the event name is mapped to a
  status label via the table in §6.
- Every response must carry
  `X-Pathao-Merchant-Webhook-Integration-Secret: f3992ecc-59da-4cbe-a049-a13da2018d51`
  and answer `202`. Pathao treats a reply without that header as a failed
  integration. The value is a protocol constant published in Pathao's own
  source, not a secret.
- Event `webhook_integration` is a handshake sent when the merchant saves the
  URL. It names no parcel and is acknowledged without being processed.

No event id and no event timestamp are published. ecomsbd dedupes on the body
hash and never fabricates an `occurred_at`.

## 6. Events and statuses

| Event | `order_status` label |
| --- | --- |
| `order.created` | `Order_Created` |
| `order.updated` | `Order_Updated` |
| `order.pickup-requested` | `Pickup_Requested` |
| `order.assigned-for-pickup` | `Assigned_for_Pickup` |
| `order.picked` | `Picked` |
| `order.pickup-failed` | `Pickup_Failed` |
| `order.pickup-cancelled` | `Pickup_Cancelled` |
| `order.at-the-sorting-hub` | `At_the_Sorting_HUB` |
| `order.in-transit` | `In_Transit` |
| `order.received-at-last-mile-hub` | `Received_at_Last_Mile_HUB` |
| `order.assigned-for-delivery` | `Assigned_for_Delivery` |
| `order.delivered` | `Delivered` |
| `order.partial-delivery` | `Partial_Delivery` |
| `order.returned` | `Return` |
| `order.delivery-failed` | `Delivery_Failed` |
| `order.on-hold` | `On_Hold` |
| `order.paid-return` | `paid_return` |
| `order.exchanged` | `exchange` |
| `order.paid` | `Payment_Invoice` |

Pathao publishes the labels and no description of what any of them settles. The
mapping onto ecomsbd's consignment lifecycle, and the reasoning for each
conservative choice, lives in `app/couriers/pathao/mapping.py`. Three are worth
repeating because the convenient mapping is the wrong one in each case:

- `Delivery_Failed` is **not** terminal — Pathao re-attempts or returns.
- `Pickup_Cancelled` is **not** `CANCELLED` — that is terminal and touches
  stock, and Pathao states no such thing.
- `paid_return` is **not** `RETURNED` — `Return` is the documented return
  outcome; `paid_return` settles nothing we can evidence.

## 7. Store and location record fields

From `js/ptc-location-manager.js`:

- store: `store_id`, `store_name`
- city: `city_id`, `city_name`
- zone: `zone_id`, `zone_name` (grouped by `city_id`)
- area: `area_id`, `area_name` (grouped by `zone_id`)

**LIST_ENVELOPE_DEPTH: UNVERIFIED.** Pathao nests list payloads at different
depths per endpoint — the plugin's own JavaScript reaches through `data`,
`data.data` and `data.data.data` for different calls. ecomsbd walks down to the
first list of objects rather than guessing one depth, and keeps every record's
raw dict.

## 8. What Pathao does not state

`STATUS_LOOKUP_CONTRACT`, `PROVIDER_CREATE_IDEMPOTENCY`, `RATE_LIMIT_CONTRACT`,
`PAGINATION_CONTRACT`, `ERROR_BODY_CONTRACT`, `BULK_RESPONSE_SCHEMA`,
`DUPLICATE_MERCHANT_ORDER_ID_BEHAVIOUR`, `TOKEN_LIFETIME`, `ITEM_WEIGHT_RANGE`,
`LIST_ENVELOPE_DEPTH`, `SETTLEMENT_BEHAVIOUR`.

The first one is the gap that changes behaviour: with no status-lookup path,
a Pathao parcel advances only when a webhook arrives. Webhook registration is
therefore operationally required for Pathao, not optional. Steadfast's polling
path is unaffected.
