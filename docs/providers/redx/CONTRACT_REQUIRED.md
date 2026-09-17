# RedX — API documentation request

**Blocker:** `REDX_API_DOCUMENTATION_REQUIRED`
**Status as of 2026-09-18:** no RedX-published API documentation obtained.

This file is the request to send RedX. It is written so one message gets
everything needed, rather than three rounds of email while sellers wait.

---

## Why this is blocking

ecomsbd will not call a courier using field names taken from a community
package. Two reasons, both learned the expensive way:

1. **Community contracts are wrong often enough to matter.** Pathao was
   implemented from Pathao's own published integration source. The
   widely-repeated community contract for Pathao's *login endpoint* — the very
   first call — is wrong: it describes `POST /aladdin/api/v1/issue-token` with
   `grant_type`, `username` and `password`, while Pathao's own plugin uses
   `POST /aladdin/api/v1/external/login` with `client_id` and `client_secret`.
   Building from the community version would have asked sellers for their
   Pathao account password and then failed.

2. **The failure is asymmetric.** A wrong field name on a read fails
   harmlessly. A wrong field name on a *create* either fails, or ships a real
   parcel to the wrong place and bills the seller for it. RedX pairs a
   delivery-area name with an area id, which is precisely the shape that
   misroutes silently instead of erroring.

Until this is answered, RedX shops use manual courier mode, which covers RedX
completely — the seller records the parcel and tracking code by hand and
uploads the RedX statement, and reconciliation runs on it exactly as on
API-sourced payments. They lose automation, not capability.

## What was already checked (so nobody repeats it)

| Checked | Result |
| --- | --- |
| `openapi.redx.com.bd/` | Live, answers JSON. `404 {"message": "Please check your specified endpoint and request method"}` |
| `/docs`, `/api-docs`, `/swagger.json`, `/openapi.json`, `/redoc`, `/.well-known/openapi.json` | All 404 — no machine-readable spec published |
| `redx.com.bd/api-documentation` | `301` → `/404/` |
| `merchant.redx.com.bd` | Does not resolve from our network |
| RedX Shopify app | Published by ShopUp (RedX's parent); listing carries no contract |
| Laravel / WooCommerce / nopCommerce packages | Third party, not promoted to a contract |

RedX appears to supply API details to merchants through the merchant panel or
an account manager. **That is the manual action required:** obtain the RedX
merchant API documentation for our account.

---

## The request to send RedX

> We are integrating RedX into our order-management platform and need the
> merchant API documentation for our account. Could you send the current
> version, including the items below?

### 1. Connection
- Base URL(s) for production, and whether a **sandbox** host exists.
- Authentication: the exact header or parameter **name**, the credential
  format, and whether the credential **expires or is refreshed** (and if so,
  how).
- Where a merchant obtains and rotates the credential.

### 2. Create parcel
- Path and HTTP method.
- **Every request field**, with which are required and which optional, and the
  unit of each numeric field (taka vs paisa, kg vs grams).
- Success response shape, including the parcel/tracking identifier.
- Whether a **merchant-supplied reference** is accepted, and **what happens
  when the same reference is sent twice.** This is the idempotency answer and
  it is the single most important item on this list — it decides whether a lost
  response can be safely resolved or becomes a duplicate parcel.
- The **failure** response body shape.

### 3. Delivery area
- Whether a sufficiently complete **free-text address** is accepted, or whether
  a structured area id is required.
- If an area id is required: the endpoint that lists areas, its response shape,
  and how a name maps to an id.

### 4. Pickup stores
- Whether a pickup store must be selected on each parcel.
- The endpoint that lists them, and the response shape.

### 5. Status
- Status lookup path and method.
- **The complete list of status strings**, and for each one: what it means for
  the parcel, and whether it is final. In particular, which statuses mean COD
  has been collected and is owed to the merchant.
- Whether partial delivery exists, and if so whether quantities are reported.

### 6. Cancel and return
- Whether either is supported, and the exact contract for each.

### 7. Money
- Which figures the API reports: delivery charge, COD amount collected, return
  fee, any other deduction.
- Whether a **create** response carries a fee, and if so whether it is a quote
  or a committed charge.

### 8. Webhook
- Whether RedX sends callbacks.
- If so: the **signature scheme** (header name and algorithm), the payload
  shape, the complete event list, the retry behaviour, and what response RedX
  expects.

### 9. Settlement
- Whether payouts/settlements can be read through the API, and the response
  shape.
- Whether a payout can be expanded into the parcels it covers.

### 10. Operational limits
- Rate limits, pagination, and bulk-create limits if bulk is supported.

---

## What happens when it arrives

The work is bounded, and no shared or V1 code needs to change:

1. Transcribe the document into `backend/app/couriers/redx/contract.py`,
   replacing the recorded absence with constants.
2. Commit the sanitized source alongside it at
   `docs/providers/redx/`, and write the normalized reading as
   `CONTRACT.md` — the same shape as `docs/providers/pathao/CONTRACT.md`.
3. Fill in `backend/app/couriers/redx/adapter.py` behind the
   `CourierAdapter` interface it already implements, reusing
   `backend/app/couriers/http.py` for transport, the error taxonomy and the
   reached-provider determination.
4. Add a `redx` entry to `backend/app/couriers/credentials.py`; the mobile and
   web connect forms render from it with no client change.
5. Promote only the capabilities the document describes in
   `docs/provider_notes/redx.yaml`, each with its evidence.
6. Turn on the `redx_enabled` feature flag per tenant.

Every capability the document does **not** describe stays `unknown` and keeps
reporting as unavailable. That is the rule that makes the manifest worth
reading.
