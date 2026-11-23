# Alpha SMS (sms.net.bd) — normalized API contract

Provider: Alpha SMS, `sms.net.bd`. Used by V4 WP7.1 (`app/sms_providers/alpha_sms.py`,
manifest `backend/app/sms_providers/manifests/alpha_sms.yaml`).

**Source for every section below:** the provider's own API page,
<https://portal.sms.net.bd/api> (the "SMS API" page of the signed-in Alpha SMS portal),
saved by the operator from the provider's domain and read on **2026-11-23**. The saved
page is not committed: it carries the account holder's name. It holds no API key (the key
field was empty when saved). No blog, plugin or package was used.

Anything this page does not state is `unknown` in the manifest and is not implemented.

## 1. Authentication (source: portal.sms.net.bd/api, 2026-11-23)

- One credential: `api_key`, "used to authenticate in our system". Generated in the portal
  ("Generate Key"). Sent as a request parameter on every call.
- No token, expiry, rotation endpoint, IP allow-list or request signing is described.

## 2. Send SMS (source: portal.sms.net.bd/api, 2026-11-23)

- Endpoint: `https://api.sms.net.bd/sendsms`
- Method: the page shows a GET query example and POST form examples (cURL `-d`, Python
  `requests` `data=`, PHP `CURLOPT_POSTFIELDS`, Node `formData`). ecomsbd uses **POST, form
  fields**, so the key is never in a URL.
- Parameters:

| Name | Required | Meaning (as documented) |
|---|---|---|
| `api_key` | yes | your API key |
| `msg` | yes | message body |
| `to` | yes | recipient numbers, comma-separated; "must start with country code (880) or Standard 01X" |
| `schedule` | no | `Y-m-d H:i:s` send time |
| `sender_id` | no | an approved Sender ID, used as "from" |
| `content_id` | no (required for bulk SMS) | an approved campaign content id |

- Success response:

```json
{"error": 0, "msg": "Request successfully submitted", "data": {"request_id": 0000}}
```

- Not stated: idempotency key or duplicate protection, maximum length, Unicode / Bangla
  handling, segment counting, per-segment price, HTTP status codes, rate limits, delivery
  callbacks.

## 3. Report (source: portal.sms.net.bd/api, 2026-11-23)

- Endpoint: `https://api.sms.net.bd/report/request/{id}/`, parameter `api_key`
  (example: query string).
- Response: `data.request_id`, `data.request_status` (example `"Complete"`),
  `data.request_charge` (decimal string, example `"0.0000"`), `data.recipients[]` with
  `number`, `charge`, `status` (example `"Sent"`). The full list of statuses and the
  charge's unit are **not stated**. Not used by ecomsbd yet.

## 4. Balance (source: portal.sms.net.bd/api, 2026-11-23)

- Endpoint: `https://api.sms.net.bd/user/balance/`, parameter `api_key` (example: query
  string). Response `data.balance` (decimal string). Not used: the documented form puts the
  key in a URL.

## 5. Error codes (source: portal.sms.net.bd/api, 2026-11-23)

The `error` field of the JSON body. How ecomsbd treats each one:

| Code | Documented meaning | ecomsbd outcome |
|---|---|---|
| 0 | Success | `SENT` (`request_id` kept as the provider reference) |
| 400 | missing or invalid parameter | `REJECTED` |
| 403 | no permission for the request | `REJECTED` |
| 404 | resource not found | `REJECTED` |
| 405 | authorization required | `REJECTED` |
| 409 | unknown error on the server | `UNKNOWN` (may have been sent; never retried) |
| 410 | account expired | `REJECTED` |
| 411 | reseller account expired or suspended | `REJECTED` |
| 412 | invalid schedule | `REJECTED` |
| 413 | invalid Sender ID | `REJECTED` |
| 414 | message is empty | `REJECTED` |
| 415 | message is too long | `REJECTED` |
| 416 | no valid number found | `REJECTED` |
| 417 | insufficient balance | `FAILED` (refused before sending; retried with backoff) |
| 420 | content blocked | `REJECTED` |
| 421 | only the registered number until the first recharge | `REJECTED` |
| any other | not documented | `UNKNOWN` |

Transport results that are not a documented JSON answer: connection refused / connect
timeout (the request never left) → `FAILED`; a read timeout, dropped connection or a body
that is not the documented JSON → `UNKNOWN` (the provider may have accepted it; ecomsbd
never re-sends it, because the API has no idempotency key).
