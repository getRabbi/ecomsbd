# Runbook — Shop referrals (V4 WP7.4)

A shop shares its referral code. Another shop enters it before its first payment.
When that shop's **first bKash / Nagad TrxID payment is approved** (WP2.5), both
shops get **30 days of plan credit** through the admin-manual grant: the same path
as support credit, so it is audited (`billing.manual_grant`), never auto-renews and
is added after any time already paid. No money moves and no provider is called.

Code: `backend/app/referrals/` (models, service, `jobs.py` outbox handler), routes
`app/api/v1/referrals.py` and `GET /v1/admin/referrals` in `app/api/v1/admin.py`;
the hook is the last step of `ManualPaymentReview.approve`
(`app/billing/manual_payments.py`). Web: Plans → "Refer a shop"
(`apps/web/components/Referral.tsx`). Tables `referral_codes`, `referrals`
(migration `a47001`).

## Switches

| What | Where | Default |
|---|---|---|
| Feature + kill switch | flag `referrals_enabled` (global or per shop) | off |
| Also needed | everything the TrxID payment needs (runbook `MANUAL_PAYMENTS.md`): `manual_payments_enabled`, `MANUAL_PAYMENT_ACCOUNTS`, `FREE_LAUNCH_MODE=false`, not a Play build | — |

`GET /v1/referrals` returns `available` and, when off, `unavailable_reason`:
`FEATURE_OFF` (referrals), `PAYMENTS_OFF`, `FREE_LAUNCH`, `CHANNEL` or `NO_ACCOUNTS`.
Referrals follow the TrxID gate because the credit only ever follows such a
payment; the phone app has no referral screen (its store builds never offer TrxID
payments).

## How it works

1. **Code** — `POST /v1/referrals/code` (owner, `billing.manage`) makes the shop's
   one code: 8 characters, no 0/O/1/I/L. Repeats return the same code.
2. **Join** — `POST /v1/referrals/apply {"code"}` by the other shop's owner. One
   `referrals` row, owned by the **referrer** (`tenant_id`), with
   `referred_tenant_id` unique. Refused with `details.code`:
   `REFERRAL_CODE_INVALID` / `REFERRAL_CODE_UNKNOWN` (also a suspended or deleted
   shop's code) / `REFERRAL_OWN_SHOP` / `REFERRAL_SAME_OWNER` (an owner of both
   shops) / `REFERRAL_CYCLE` (the shop it referred) → 422;
   `REFERRAL_ALREADY_APPLIED` / `REFERRAL_AFTER_FIRST_PAYMENT` (an approved TrxID
   payment or a verified store purchase exists) → 409. The same code again → 200.
   10 tries per shop per hour (429 after).
3. **Reward** — approving a TrxID payment writes outbox `referral.payment_approved`
   (`{submission_id}`). The handler, in a system session, locks the shop's referral:
   * PENDING and flag on → the qualifying payment is the shop's **earliest
     approved** TrxID payment. Both shops get
     `grant_manual_subscription(days=30)` with `provider_event_id`
     `referral:<id>:referred` / `referral:<id>:referrer` (unique per provider: a
     second grant cannot be written). Plan: the shop's current paid plan, or, for
     a Free shop, the plan the referred shop paid for. Row → `REWARDED`.
   * The referrer pays through a billing provider (Play, bKash web, current and
     not Free) → its credit is `HELD` / `PROVIDER_SUBSCRIPTION` (a manual grant
     would replace the provider's row and be undone on its next sync). A referrer
     shop that is not ACTIVE → `HELD` / `SHOP_NOT_ACTIVE`. The referred shop is
     still credited.
   * A verified store purchase before the first TrxID payment → `VOID` /
     `NOT_FIRST_PAYMENT`, nothing granted.
   * Flag off for the referred shop → nothing granted, the row stays PENDING; the
     shop's next approved payment looks again (and rewards the first one).
   * Not PENDING (already rewarded / void) → nothing. Retries are safe.

The referrer sees its referrals as states and dates only (never the other shop's
name); the referred shop sees the code it used and its own credit state.

## Detection

* Held referrer credits: `GET /v1/admin/referrals?referrer_credit=HELD`
  (`admin.billing_read`). Each is a decision for support (below).
* Rewards: `GET /v1/admin/referrals?state=REWARDED`; audit actions
  `referral.applied`, `referral.rewarded` and the two `billing.manual_grant`
  entries; log lines `referral rewarded` / `referral void: not the first payment`
  / `referral reward waits: feature off` (ids only).
* A stuck `referral.payment_approved` outbox event (handler error) shows in the
  outbox backlog like any other topic (runbook `WEBHOOK_BACKLOG.md` → Outbox; a
  dead-lettered event needs the `retry_outbox_event` repair). The approval itself is already committed: a referral problem never
  undoes a payment.
* Abuse signal: many referrals from one referrer whose referred shops pay once
  and stop. Compare `GET /v1/admin/referrals?tenant_id=<referrer>` with the
  shops' payment history.

## Containment

* Stop all new credits: turn `referrals_enabled` off globally (admin console flag
  repair). Codes stop being shown or accepted; pending referrals wait; nothing is
  revoked.
* One shop: a per-shop `referrals_enabled=false` row for that shop.
* A wrong credit: there is no automatic reversal (the grant is plan time, not
  money). Shorten it by hand only after deciding with the seller; record the
  reason in the support case.

## Recovery

* HELD `PROVIDER_SUBSCRIPTION`: the referrer's plan renews through the store. If
  the 30 days should still be given, wait until the store subscription lapses (or
  the seller moves to TrxID payments), then grant with the support-credit repair —
  `POST /v1/admin/repairs/grant_support_credit` with
  `{"reason": "referral <referral id>", "tenant_id": "<referrer>",
  "params": {"plan": "<plan>", "days": 30}}` (`BILLING_GRANT`, SUPERADMIN).
* HELD `SHOP_NOT_ACTIVE`: grant the same way only if the shop is reinstated.
* A handler that keeps failing: read the event's error, fix, let the dispatcher
  retry. The row only becomes REWARDED in the same transaction as both grants.

## Verification (staging)

1. TrxID payments working (runbook `MANUAL_PAYMENTS.md` → Verification); turn
   `referrals_enabled` on for two test shops (A, B) with different owners.
2. A's owner: web Plans → "Get my code"; copy it.
3. B's owner: Plans → "Have a code from another shop?" → enter it → "You joined
   with code …".
4. B pays by TrxID; approve it (`POST /v1/admin/payments/{id}/approve`).
5. After the worker runs: B's plan end = the paid end + 30 days; A's plan has 30
   more days (or A is on the paid plan B bought, for 30 days, if A was Free); A's
   Plans card shows the shop "First payment approved · Your 30 days: added";
   `GET /v1/admin/referrals?state=REWARDED` lists it with both credits `GRANTED`.
6. Approve a second payment of B: nothing more is granted.
