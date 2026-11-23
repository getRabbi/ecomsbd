# BD Courier (bdcourier.com) — normalized API contract

Provider: BD Courier, `bdcourier.com` ("কাস্টমার ভেরিফিকেশন ও রিস্ক চেক"). Used by V4 WP0.6
(`backend/app/risk_providers/bdcourier.py`) behind the existing external-risk port
(`backend/app/risk_providers/contract.py`).

**Source for every section below:** the provider's own developer page
<https://bdcourier.com/api-docs>, fetched on **2026-11-23**. The page is a client-rendered
React app: the HTML (`/api-docs`, sha256 `7815f0be…e4cc`) only loads
`https://bdcourier.com/site/assets/index-DDbkeRIl.js` (244,855 bytes, sha256
`c380fc011a1056016879d98dbbf47332f7cbfa61d8e35ba9ab4fd7e3c1ecdf31`), and that bundle holds the
page's text, tables, code samples and the sample response quoted here. The terms
(§6) come from the same bundle (route `/terms-of-service`). No blog, plugin, package or
third-party page was used. The bundle is not committed (BD Courier's own work, §11 of
its terms); a later bundle with another hash means the page changed: re-read it.

Anything the page does not state is **unknown** and is not implemented.

## 1. Base URL and authentication (source: bdcourier.com/api-docs, 2026-11-23)

- Base URL, "সকল API রিকোয়েস্টের জন্য": `https://api.bdcourier.com`
- "সকল প্রোটেক্টেড এন্ডপয়েন্টে API Key পাঠাতে হবে।" Three ways are listed:
  `Authorization: Bearer YOUR_KEY`, `X-API-Key: YOUR_KEY`, `?api_key=YOUR_KEY`.
  ecomsbd sends **only** `Authorization: Bearer`, so the key is never in a URL.
- Key: "ড্যাশবোর্ডের Settings থেকে আপনার সিক্রেট API Key কপি করুন।" Accounts are opened at
  `https://app.courier.com.bd/register` (the page's "ফ্রি API Key নিন" link).
- No token exchange, expiry, rotation, IP allow-list or request signing is described.

## 2. POST /courier-check — "কাস্টমার সার্চ" (source: bdcourier.com/api-docs, 2026-11-23)

"ফোন নম্বর দিয়ে কাস্টমারের সম্পূর্ণ ডেলিভারি হিস্ট্রি এবং AI-পাওয়ার্ড রিস্ক অ্যানালাইসিস পান।"
Auth required. Every code sample sends JSON (`Content-Type: application/json`):

```bash
curl -X POST "https://api.bdcourier.com/courier-check" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"phone": "01XXXXXXXXX"}'
```

Parameters:

| Name | Type | Required | Description (as documented) |
|---|---|---|---|
| `phone` | string | yes | "১১ ডিজিটের বাংলাদেশি ফোন নম্বর (যেমন: 01XXXXXXXXX)" |

Response fields (table):

| Field | Type | Description (as documented) |
|---|---|---|
| `status` | string | "success বা error" |
| `phone` | string | "সার্চ করা ফোন নম্বর" |
| `success_ratio` | number | "সফল ডেলিভারি রেট (%)" |
| `risk_level` | string | "safe, low, medium, high, danger" |
| `risk_verdict` | object | "বিস্তারিত রিস্ক অ্যানালাইসিস" |
| `data` | object | "কুরিয়ার-ওয়াইজ ডেলিভারি ডেটা" |

Sample response (verbatim, logos shortened to `…`):

```json
{
  "status": "success",
  "data": {
    "pathao":       {"name": "Pathao",       "logo": "…", "total_parcel": 150, "success_parcel": 120, "cancelled_parcel": 30, "success_ratio": 80.0},
    "steadfast":    {"name": "SteadFast",    "logo": "…", "total_parcel": 200, "success_parcel": 175, "cancelled_parcel": 25, "success_ratio": 87.5},
    "parceldex":    {"name": "ParcelDex",    "logo": "…", "total_parcel": 0,   "success_parcel": 0,   "cancelled_parcel": 0,  "success_ratio": 0},
    "courrierfast": {"name": "CourrierFast", "logo": "…", "total_parcel": 50,  "success_parcel": 45,  "cancelled_parcel": 5,  "success_ratio": 90.0},
    "redx":         {"name": "Redx",         "logo": "…", "total_parcel": 80,  "success_parcel": 65,  "cancelled_parcel": 15, "success_ratio": 81.25},
    "paperfly":     {"name": "PaperFly",     "logo": "…", "total_parcel": 100, "success_parcel": 90,  "cancelled_parcel": 10, "success_ratio": 90.0},
    "carrybee":     {"name": "CarryBee",     "logo": "…", "total_parcel": 40,  "success_parcel": 35,  "cancelled_parcel": 5,  "success_ratio": 87.5},
    "summary":      {"total_parcel": 620, "success_parcel": 530, "cancelled_parcel": 90, "success_ratio": 85.48}
  },
  "reports": [],
  "risk_verdict": {"level": "safe", "label": "Safe", "action": "OK to ship", "color": "green",
                   "reasons": ["Strong delivery success rate (85.48%)"]}
}
```

Risk levels ("রিস্ক লেভেল বুঝুন"): `safe` (বিশ্বস্ত কাস্টমার), `low`, `medium`, `high`, `danger`.

The field table and the sample disagree: the table lists top-level `phone`, `success_ratio`
and `risk_level`; the sample has none of them and carries `risk_verdict.level` and `reports`.

### How ecomsbd reads it (decisions, not provider claims)

- Sent: the customer's number as 11-digit national `01XXXXXXXXX`; nothing else about the
  customer, the order or the shop.
- Kept: per courier key in `data` (except `summary`) the three integer counts
  `total_parcel`, `success_parcel` → delivered, `cancelled_parcel` → cancelled; `summary`
  as the aggregate. Facts `TOTAL_PARCELS` and `DELIVERED_PARCELS` from `summary`.
  A courier with `total_parcel` 0 is left out (no history). A non-integer count, or
  success + cancelled above total, drops that row (`normalize_courier_history`).
- Dropped, never stored or shown: `risk_level`, `risk_verdict`, `success_ratio` (ratios are
  computed from the counts), `reports` (contents undocumented), `name`, `logo`, `phone`.
  The port carries facts, never a provider's score ("AI-powered" verdict included).
- `cancelled_parcel` is not mapped to `RETURNED_PARCELS` or `CANCELLED_ORDERS`: the page does
  not say which it is.
- `summary.total_parcel` 0 (or no courier rows) = NOT_FOUND.
- Not documented, so not claimed: data period / as-of time, rate-limit numbers, error body
  shape, HTTP status codes. Generic HTTP meaning is used: 401 / 403 = key refused,
  429 = rate limited (`Retry-After` seconds when given), 5xx / no connection = unavailable,
  timeout = timeout, other 4xx or `"status": "error"` = refused, anything unreadable = bad
  response. A lookup only reads, so the port's one retry on timeout / unavailable is safe.

## 3. GET /plans — "প্ল্যান লিস্ট" (source: bdcourier.com/api-docs, 2026-11-23)

"সকল প্রাইসিং প্ল্যানের তথ্য পান।" No auth. Response fields: `id` (number), `name`
(string), `price` (number, "মাসিক মূল্য (টাকা)"), `features` (array).

ecomsbd's connection test calls this endpoint: it proves the API answers, **not** that
the key is right (the page has no key-check endpoint, and a test `/courier-check` would be a
lookup without an order, which §6 forbids). A wrong key shows on the first real lookup as
`PROVIDER_AUTH_FAILED` and the connection turns DOWN.

## 4. Not documented (unsupported)

Bulk lookup, webhooks, per-courier date ranges, fraud reports content, sandbox host,
quota/remaining-credit endpoint, rate-limit numbers, idempotency, price per lookup.

## 5. Price (source: bdcourier.com/api-docs, 2026-11-23)

Not on the API page beyond "ফ্রি API Key নিন" / "ফ্রি অ্যাকাউন্ট খুলুন — কোনো ক্রেডিট কার্ড
লাগবে না।"; `GET /plans` returns the plan list at run time. The seller pays BD Courier
directly on their own account.

## 6. Terms that bind the integration (source: bdcourier.com/terms-of-service, same bundle, 2026-11-23)

Quoted (Bangla, verbatim) with what ecomsbd does about each:

| Terms | ecomsbd |
|---|---|
| §5 note: "SaaS / Software কোম্পানিদের জন্য: প্রতিটি User-কে অবশ্যই আমাদের Platform-এ সরাসরি Register করতে হবে এবং নিজের Individual API Key দিয়ে Integration ব্যবহার করতে হবে। কোনো White-labeling, API Rebranding অথবা কেন্দ্রীয় Backend Service-এর মাধ্যমে একাধিক Client-কে BD Courier সেবা বা ডেটা সরবরাহ করা সম্পূর্ণ নিষিদ্ধ।" | Each shop's owner opens their own BD Courier account and pastes their own key (per-shop, encrypted, never shown again). ecomsbd has **no platform key**, no env variable for one, and never uses one shop's key or answer for another shop. The product shows the provider by name (no rebranding). |
| §5 list: "Reselling … SaaS Integration for Resale … Third-party Customer Access প্রদান" need BD Courier's prior written permission | Operator action: confirm with BD Courier (support@bdcourier.com) that a seller using their own key inside ecomsbd is allowed, before turning `bdcourier_enabled` on for anyone but a test shop. |
| §3 "BD Courier-এর প্রতিটি অনুসন্ধান অবশ্যই একটি প্রকৃত, সদ্য প্রাপ্ত এবং যাচাইয়ের অপেক্ষায় থাকা অর্ডারের সাথে সম্পর্কিত হতে হবে।" Not allowed: "শুধুমাত্র ফোন নম্বর যাচাই \| কাস্টমার স্কোরিং \| বিদ্যমান গ্রাহক ডেটাবেস বিশ্লেষণ …" | A BD Courier lookup is refused (`NEW_ORDER_REQUIRED`, nothing sent) unless the customer has an order in DRAFT or CONFIRMED created in the last 3 days. |
| §4.2 / §6 "Bulk Lookup করা যাবে না \| Background Scanning করা যাবে না \| Customer Database Sync করা যাবে না …" | Lookups happen only when a seller taps Check / Refresh. No job, workflow or import calls the provider; automation only reads stored answers. Daily cap per shop (200) and the 10-minute refresh floor stay. |
| §7 "স্থায়ী ডেটাবেস তৈরিতে ব্যবহার করা যাবে না", "তৃতীয় পক্ষকে সরবরাহ করা যাবে না" | Answers are cached for the shop only (default 24 h, max 72 h) and deleted after 30 days (prune job). Never shared with the anonymous network or other shops. |
| §8 "API Key … অন্যের সাথে শেয়ার করা নিষিদ্ধ।" | The key is stored encrypted per shop, never returned by the API, never logged. |
| §9 results are "শুধুমাত্র সহায়ক বিশ্লেষণ" | Shown as attributed provider facts beside the shop's own Risk Check; never decides an order. |
