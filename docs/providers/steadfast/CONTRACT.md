# Steadfast Courier V1 — normalized provider contract

**Source document:** `API_Documentation_V1.sanitized.html` in this directory — a
sanitized, redacted copy of "API Documentation.html", the Google Docs export of
Steadfast Courier Limited's V1 API documentation supplied by the operator.
**Documentation version:** V1. **Read and transcribed:** 2026-09-11.

This file is the *only* thing the adapter is allowed to implement against. It
contains what the supplied document explicitly says, and marks everything else
`UNDOCUMENTED`. Where an earlier ecomsbd assumption disagrees with the supplied
document, the document wins.

Three reading rules were applied while transcribing:

1. A field is documented only if the document names it. A field visible only in
   a sample JSON body is recorded as **sample-only** — real, but with no stated
   type, nullability or constraint.
2. An endpoint with a path and a method but no request or response schema is
   recorded as **path-only**. It is implemented as a raw, tolerant fetch; its
   fields are not invented.
3. Nothing about idempotency, rate limits, pagination, retries, webhooks or
   authentication lifetime is stated anywhere in the document. All of it is
   `UNDOCUMENTED`, and none of it is guessed.

---

## Authentication

| Header | Type | Value |
|---|---|---|
| `Api-Key` | string | API key provided by Steadfast Courier Ltd. |
| `Secret-Key` | string | Secret key provided by Steadfast Courier Ltd. |
| `Content-Type` | string | `application/json` |

> "Authentication parameters are required to be added at the header part of each
> request."

**Base URL:** `https://portal.packzy.com/api/v1`

Static merchant key/secret headers on every request. The document says nothing
about tokens, refresh, expiry, rotation or scopes, so the client implements none
of those: `STEADFAST_AUTH_LIFETIME = UNDOCUMENTED`.

---

## 1. Create single order

`POST /create_order`

| Field | Type | Required | Documented constraint |
|---|---|---|---|
| `invoice` | string | required | "Must be Unique and can be alpha-numeric including hyphens and underscores." Examples: `12366`, `abc123`, `12abchd`, `Aa12-das4`, `a_sdfd-wq` |
| `recipient_name` | string | required | Within 100 characters |
| `recipient_phone` | string | required | Must be 11 digits phone number (example `01234567890`) |
| `alternative_phone` | string | optional | Must be 11 digits phone number |
| `recipient_email` | string | optional | — |
| `recipient_address` | string | required | Within 250 characters |
| `cod_amount` | numeric | required | "Cash on delivery amount in BDT including all charges. Can't be less than 0." |
| `note` | string | optional | Delivery instructions or other notes |
| `item_description` | string | optional | Items name and other information |
| `total_lot` | numeric | optional | Total lot of items |
| `delivery_type` | numeric | optional | `0` = home delivery, `1` = Point Delivery / Steadfast Hub Pick Up |

Documented success response:

```json
{
  "status": 200,
  "message": "Consignment has been created successfully.",
  "consignment": {
    "consignment_id": 1424107,
    "invoice": "Aa12-das4",
    "tracking_code": "15BAEB8A",
    "recipient_name": "John Smith",
    "recipient_phone": "01234567890",
    "recipient_address": "…",
    "cod_amount": 1060,
    "status": "in_review",
    "note": "Deliver within 3PM",
    "created_at": "2021-03-21T07:05:31.000000Z",
    "updated_at": "2021-03-21T07:05:31.000000Z"
  }
}
```

`status` is carried **in the body**, not only in the HTTP status line. The client
therefore treats a `200` body status as success and anything else as a failure
even on an HTTP 200 — see "Protocol notes" below.

**Not documented:** the failure body shape, validation error codes, what happens
when `invoice` is reused, per-account rate limits, or whether a retry of the
same `invoice` is idempotent. `STEADFAST_CREATE_IDEMPOTENCY = UNKNOWN`.

`cod_amount` is documented as the amount to collect "including all charges". The
document gives **no decomposition** of that figure into delivery charge, COD fee
or anything else, and the create response echoes only `cod_amount`. The adapter
therefore records **no** courier charge from a booking response.

---

## 2. Create bulk order

`POST /create_order/bulk-order`

| Field | Type | Required | Documented constraint |
|---|---|---|---|
| `data` | JSON | required | "Maximum 500 items are allowed. Json encoded array" |

The document's own PHP example sends `data` as a **JSON-encoded string** inside a
JSON body (`json_encode($data)` passed as the `data` field), and its per-item
keys are the same names as the single-create fields. Both facts are taken from
the sample code, which is the only description given.

Documented success result — a bare JSON array, one entry per submitted item:

```json
[
  {
    "invoice": "230822-1",
    "recipient_name": "John Doe",
    "recipient_address": "…",
    "recipient_phone": "0171111111",
    "cod_amount": "0.00",
    "note": null,
    "consignment_id": 11543968,
    "tracking_code": "B025A038",
    "status": "success"
  }
]
```

Documented error result — "If there is any error in data your will get response
like", showing the same items wrapped in a `data` key, with
`consignment_id: null`, `tracking_code: null`, `status: "error"`:

```json
"data": [
  { "invoice": "230822-1", "…": "…", "consignment_id": null, "tracking_code": null, "status": "error" }
]
```

So the parser must accept **both** a bare array and an object carrying `data`,
and treat per-item `status` as the item's outcome. Item-level `status` values
seen in the document: `success`, `error`.

**Not documented:** an error *reason* per item, whether the response preserves
submission order, whether a partially-failing batch is atomic, or what happens to
items after the 500th. ecomsbd applies its own smaller chunk size and matches
results back by `invoice`, never by position — see `IMPLEMENTATION.md`.

---

## 3. Delivery status lookup

Three forms, all `GET`, all returning the same body:

| Form | Path |
|---|---|
| By consignment id | `/status_by_cid/{id}` |
| By your invoice id | `/status_by_invoice/{invoice}` |
| By tracking code | `/status_by_trackingcode/{trackingCode}` |

```json
{ "status": 200, "delivery_status": "in_review" }
```

**Not documented:** the body returned when the reference does not exist, any
timestamp for when the status changed, or any history. The absence of a
documented "not found" body is why a status lookup can never *prove* a parcel
does not exist — see `IMPLEMENTATION.md`, booking recovery.

### Documented delivery statuses (complete list, verbatim)

| Value | Document's description |
|---|---|
| `pending` | Consignment is not delivered or cancelled yet. |
| `delivered_approval_pending` | Consignment is delivered but waiting for admin approval. |
| `partial_delivered_approval_pending` | Consignment is delivered partially and waiting for admin approval. |
| `cancelled_approval_pending` | Consignment is cancelled and waiting for admin approval. |
| `unknown_approval_pending` | Unknown Pending status. Need contact with the support team. |
| `delivered` | Consignment is delivered and balance added. |
| `partial_delivered` | Consignment is partially delivered and balance added. |
| `cancelled` | Consignment is cancelled and balance updated. |
| `hold` | Consignment is held. |
| `in_review` | Order is placed and waiting to be reviewed. |
| `unknown` | Unknown status. Need contact with the support team. |

Eleven values, exactly. Note what the document does **not** say: there is no
`in_transit`, no `picked_up` and no `out_for_delivery`. Anything before delivery
is `pending` or `in_review`. ecomsbd's own richer lifecycle keeps those states
for manual mode, and a Steadfast parcel simply never enters them.

Note also that the approval-pending descriptions are explicit that balance is
added on `delivered` / `partial_delivered` / `cancelled` and **not** on the
`*_approval_pending` values. That is the documented basis for treating
approval-pending as provisional.

`partial_delivered` carries **no quantities**. The status string is the entire
payload. Quantities must come from a person.

---

## 4. Current balance

`GET /get_balance`

```json
{ "status": 200, "current_balance": 0 }
```

The document calls it the merchant's current balance and gives no currency, no
unit, no breakdown and no statement of what it includes. It is the provider's
own account figure and is never treated as an ecomsbd COD receivable.

---

## 5. Create return request

`POST /create_return_request`

| Field | Type | Documented constraint |
|---|---|---|
| `consignment_id` **or** `invoice` **or** `tracking_code` | numeric or string | "Required. … Consignment id or user defined invoice id or tracking code of the consignment of the requesting consignment." |
| `reason` | string | Optional |

Documented response fields, given as a value table:

| Field | Example |
|---|---|
| `id` | `1` |
| `user_id` | `1` |
| `consignment_id` | `10000042` |
| `reason` | `null` |
| `status` | `pending` |
| `created_at` | `2025-07-30T23:11:45.000000Z` |
| `updated_at` | `2025-07-30T23:11:45.000000Z` |

### Documented return-request statuses (complete list, verbatim)

`pending`, `approved`, `processing`, `completed`, `cancelled`

**Not documented:** whether the response is wrapped in an envelope, whether a
second request for the same consignment is rejected or silently duplicated, or
what an error looks like. ecomsbd prevents duplicates on its own side.

---

## 6. Single return request

`GET /get_return_request/{id}` — **path-only.** No request parameters and no
response schema are documented.

## 7. List return requests

`GET /get_return_requests` — **path-only.** No pagination parameter, filter or
response schema is documented.

## 8. Get payments

`GET /payments` — **path-only.** No pagination parameter, no filter, no response
schema, no field list, no sample body.

## 9. Get single payment with consignments

`GET /payments/{payment_id}` — **path-only.** The title states the response
includes consignments. No field list, no nested schema and no sample body are
given.

## 10. Get police stations

`GET /police_stations` — **path-only.** No request parameters and no response
schema are documented.

> The four path-only sections above are the honest limit of the supplied
> document. Sections 6–10 of the document's own table of contents give a path
> and a method and stop. `IMPLEMENTATION.md` records how ecomsbd integrates them
> completely without inventing their fields: typed known keys where the *purpose*
> of the endpoint fixes them, tolerant preservation of every unknown key, and
> raw payload retention so the real shape is captured the first time a live
> account answers.

---

## Protocol notes

* **Body status, not HTTP status.** Every documented response carries
  `"status": 200` inside the body. The document never states what HTTP status
  accompanies a failure, so the client checks both and treats a mismatch as a
  provider protocol error rather than as success.
* **`cod_amount` types are inconsistent in the document's own samples** —
  `1060` (number) in the single-create response, `"0.00"` (string) in the bulk
  result. The parser accepts both.
* **`cod_amount` is sent as whole taka.** The document types it `numeric` and
  its example is `1060`, which is taka. ecomsbd stores paisa, so the boundary
  rounds half-up to whole taka — a courier collects banknotes, and poisha coins
  are out of circulation, so a COD of ৳1050.50 cannot be collected as stated
  whatever the field type permits. The paisa residual is **returned by
  `provider_cod_taka()` and recorded on the booking attempt**, not discarded:
  an unexplainable few-paisa reconciliation difference months later is exactly
  what silent rounding produces.
* **Phone format.** ecomsbd stores `+8801XXXXXXXXX` (E.164). Steadfast documents
  an 11-digit national number. The transformation is applied at the adapter
  boundary only; the canonical store is unchanged.
* **`total_lot` and `delivery_type` are numeric**, not boolean or string.
* **Bangla numerals** never reach the provider: normalization happens well
  upstream, in `app.common.phone`.

## `UNDOCUMENTED` — the complete list

Nothing below appears anywhere in the supplied document. None of it is
implemented from memory, a community SDK or a blog post.

| Topic | State |
|---|---|
| Webhooks — any endpoint, signature, header, payload, retry contract, event id | `UNDOCUMENTED` |
| Create-order idempotency, duplicate-`invoice` behaviour | `UNDOCUMENTED` |
| Rate limits, throttling, `429` semantics, `Retry-After` | `UNDOCUMENTED` |
| Pagination for `/payments`, `/get_return_requests`, `/police_stations` | `UNDOCUMENTED` |
| Error response bodies, provider error codes | `UNDOCUMENTED` |
| Price quote / delivery-charge estimation endpoint | `UNDOCUMENTED` (no such endpoint exists in the document) |
| Customer delivery-history / fraud-check endpoint | `UNDOCUMENTED` (no such endpoint) |
| Cancel-a-consignment endpoint | `UNDOCUMENTED` (no such endpoint) |
| Pickup store / warehouse listing | `UNDOCUMENTED` (no such endpoint) |
| Status-change timestamps or status history | `UNDOCUMENTED` |
| Per-parcel courier charge breakdown | `UNDOCUMENTED` |
| Auth token lifetime, refresh, rotation | `UNDOCUMENTED` |
| Sandbox / staging base URL | `UNDOCUMENTED` |
