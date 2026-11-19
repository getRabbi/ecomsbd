# Runbook — Seller payment by bKash / Nagad TrxID (V4 WP2.5)

A seller sends the plan price to the operator's own bKash or Nagad number and
types the TrxID. Nothing is granted until an operator finds that TrxID in the
receiving wallet and approves it in the admin console. No bKash or Nagad API is
called: none is contracted, so the check is a person reading the wallet.

Code: `backend/app/billing/manual_payments.py` (service), `app/billing/jobs.py`
(seller notification), routes in `app/api/v1/billing.py` and `app/api/v1/admin.py`.
Table: `billing_payment_submissions` (migration `a42001`).

## Switches

| What | Where | Default |
|---|---|---|
| Feature | flag `manual_payments_enabled` (global or per shop) | off |
| Receiving numbers | `MANUAL_PAYMENT_ACCOUNTS` (JSON list, see `.env.production.example`) | empty |
| Hidden anyway | `FREE_LAUNCH_MODE=true`; any Play build (`X-Distribution-Channel: PLAY` or `DISTRIBUTION_CHANNEL=PLAY`, master spec §27.1) | — |

`GET /v1/billing/manual-payments/options` returns `available` and, when off,
`unavailable_reason`: `FEATURE_OFF`, `FREE_LAUNCH`, `CHANNEL` or `NO_ACCOUNTS`.

## Daily review (the work queue)

1. `GET /v1/admin/payments?state=SUBMITTED` with an admin token holding
   `admin.billing_read` — oldest first. Each row: shop id, plan, months,
   `amount_paisa`, method, masked payer number, TrxID.
2. Open the receiving wallet (bKash / Nagad merchant or personal app) and find
   the TrxID. Check the amount equals `amount_paisa / 100` taka and the sender
   ends with the masked digits.
3. Found and matching → `POST /v1/admin/payments/{id}/approve` with
   `{"note": "seen in bKash app"}` (needs `admin.billing_grant`). The plan is
   granted until `period_end` = later of now and the shop's current paid end
   + 30 days × months. Approving again returns the same row and grants nothing.
4. Not found, wrong amount or wrong sender → `POST /v1/admin/payments/{id}/reject`
   with `{"reason": "..."}`. **The seller sees the reason** — write it for them,
   in plain words, no internal detail.
5. The seller gets a `BILLING_PAYMENT_REVIEWED` notification (EN + BN; no TrxID,
   number or reason on the lock screen) once the worker delivers the
   `billing.manual_payment_reviewed` outbox event.

## Detection

- Queue growing: count of `state=SUBMITTED` older than 24 h.
- Duplicates: a seller reporting `TRX_ID_ALREADY_USED` (409). The TrxID is
  unique per method across every shop — look it up with
  `GET /v1/admin/payments?tenant_id=` for both shops before answering. A TrxID
  used by two shops is a fraud signal; do not approve the second.
- Audit trail: `billing.manual_payment_submitted / _approved / _rejected`
  (`GET /v1/admin/ops/audit?action=billing.manual_payment_approved`).

## Containment

- Stop new submissions: turn `manual_payments_enabled` off (repair
  `disable_provider_capability`, global or per shop). Existing rows can still
  be reviewed.
- Wrong receiving number published: fix `MANUAL_PAYMENT_ACCOUNTS` and restart;
  an invalid entry is ignored (hides that account) rather than stopping the API.

## Recovery

- Approved by mistake: there is no "un-approve". Expire or shorten the plan
  through the existing billing support tools and refund the seller from the
  wallet; record a support case. The `billing_transactions` row stays (append-only).
- Rejected by mistake: the TrxID cannot be resubmitted (it stays used). Grant
  the time with the `grant_support_credit` repair, reason naming the TrxID.
- Notification missing: check the outbox event for the submission
  (`dedupe_key manual_payment:<id>:<state>`); re-delivery is safe (deduplicated).

## Verification (staging, gate G2)

1. Set `MANUAL_PAYMENT_ACCOUNTS`, `FREE_LAUNCH_MODE=false`, flag on for a test shop.
2. From the web Billing page (or a non-Play Android build) send a small real
   payment, submit the TrxID; the row appears in `state=SUBMITTED`.
3. Approve it; the shop's plan shows the new end date, Billing history shows a
   `PURCHASE` from `manual_admin`, and the notification arrives.
4. Write "manual payment approved end to end <date>" in the ledger Gates table.
