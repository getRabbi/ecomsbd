# Runbook — BD Courier external risk lookup (V4 WP0.6)

Code: `backend/app/risk_providers/bdcourier.py` on the existing external-risk port
(`app/risk_providers/`), contract `docs/providers/bdcourier/CONTRACT.md`
(bdcourier.com/api-docs, read 2026-11-23). Flag `bdcourier_enabled`, default **off**.
No env variable, no migration, no job of its own (the daily `prune_external_risk_lookups`
also enforces BD Courier's 30-day keep).

What it does: when a seller taps **Check with provider** on a customer who has a new
order, the shop's own BD Courier key asks `POST https://api.bdcourier.com/courier-check`
with the 11-digit number. The per-courier parcel counts become the courier-history chip
("Steadfast 175/200 · Pathao 120/150"), the facts `TOTAL_PARCELS` / `DELIVERED_PARCELS` and
the automation condition `external_success_bps`. BD Courier's own verdict (`risk_level`,
`risk_verdict`), ratio and `reports` are dropped.

## Before turning it on (terms — CONTRACT.md §6)

1. Ask BD Courier (support@bdcourier.com) in writing whether ecomsbd sellers may use their
   own keys inside ecomsbd. Their §5 note requires "নিজের Individual API Key" per user and
   forbids serving many clients from one account or a "কেন্দ্রীয় Backend Service"; §5 also
   lists "SaaS Integration for Resale" as needing prior written permission. Keep the reply.
2. Update the public privacy policy (`apps/web/app/privacy-policy/content.ts`, "No external
   risk provider is currently registered …") to say: when a shop connects its own BD Courier
   account, the customer's phone number is sent to BD Courier when the seller checks a new
   order; answers are kept for that shop only, up to 30 days. Then the Play Data safety form.
3. Never add a platform BD Courier key, and never connect one shop's key for another shop.

## Turn on (test shop first)

1. Admin console → feature flags → `bdcourier_enabled` on for the test shop only (tenant row).
2. Seller (owner) opens a BD Courier account at `https://app.courier.com.bd/register`, copies the
   API key from the BD Courier dashboard's Settings.
3. Web → Settings → Risk provider → BD Courier: read the terms note, paste the key, Save,
   **Test** (asks `GET /plans`: proves BD Courier answers; the key is checked on the first
   lookup), **Turn on**. Cache: default 24 h, allowed 1–72 h.
4. Create an order for a test number (017000000xx range is fake — use your own number for a
   real answer), open the customer → **Check with provider**. Expect the courier-history chip,
   "Checked …", and a FRESH badge. A second tap within 24 h says the stored answer is current.

## Verification

- `external_risk_lookups` has one row: provider `bdcourier`, `status` FOUND / NOT_FOUND,
  `courier_history` with lowercase courier keys only, `expires_at` = checked + TTL.
- Audit `external_risk.lookup` for the tap; no key in any log or audit context.
- A customer whose newest order is older than 3 days, or already PACKED / shipped / cancelled:
  the tap says "this provider checks only a customer with a new order"; no request is sent,
  no row is written.

## Detection

- Settings → Risk provider shows health DEGRADED / DOWN, last error code.
- `PROVIDER_AUTH_FAILED` on the first lookup = the seller's key is wrong or was revoked by
  BD Courier (their §14: they may cancel a key without notice) → ask the seller to check
  their BD Courier account.
- `PROVIDER_RATE_LIMITED` = BD Courier answered 429; lookups pause until `rate_limited_until`.
- Outbox `external_risk.provider_unavailable` events for the shop.

## Containment

- One shop: Settings → Risk provider → Turn off (owner), or tenant flag row off.
- Everyone: global `bdcourier_enabled` off. With the flag off the provider disappears from
  the catalog, no lookup is made, stored answers stop showing and stop feeding
  `external_success_bps` and the confirmation policy; rows are still pruned on schedule.

## Recovery

- Wrong key: seller pastes the new key → Test → Turn on (a new key always needs a test).
- BD Courier down: nothing to do; the shop's own Risk Check is unaffected and lookups resume
  after the 5-minute circuit.
- Page changed (new bundle hash, new fields): re-read bdcourier.com/api-docs, update
  CONTRACT.md first, then the parser and its tests.

## Known limits (unknown in the contract)

Rate-limit numbers, error body shape, HTTP status meanings, data period / as-of time, what
`cancelled_parcel` counts (cancelled before pickup or returned), price per lookup, sandbox.
