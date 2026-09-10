# Runbook — billing verification failure

**Master spec sections 90, 91, 93.** A seller has paid and does not have what
they paid for, or a purchase cannot be verified at all.

The rule that must not be broken while fixing this: **never grant a paid
entitlement from a client callback.** If the provider cannot confirm the
purchase, the answer is a support credit with a reason attached, not a hand-set
subscription row that nothing can explain later.

---

## Detection

| Signal | Where |
|---|---|
| `billing_verification_failures` climbing | `GET /v1/admin/ops/counts` |
| Refused transactions | `GET /v1/admin/tenants/{id}/billing`, `state = FAILED` |
| "I paid and it says Free" | support — this is the one sellers actually report |
| `billing.replay_blocked` audit entries | `GET /v1/admin/ops/audit?action=billing.replay_blocked` |

---

## Triage: read the verification result

Every refusal is recorded with **why**. Start there, not with the seller's
description.

```sql
SELECT tenant_id, provider, kind, state, verification_result,
       verification_detail, provider_product_id, occurred_at
  FROM billing_transactions
 WHERE state = 'FAILED'
   AND occurred_at > now() - interval '24 hours'
 ORDER BY occurred_at DESC;
```

| `verification_result` | What it means | What to do |
|---|---|---|
| `NOT_CONFIGURED` | This deployment has no Play service account or bKash contract. | Not a seller problem. A release blocker — see `docs/RELEASE_READINESS.md`. Issue support credit so they are not stuck. |
| `UNAVAILABLE` | The provider did not answer. **Not a rejection.** | Retry. Existing access was deliberately left untouched. If it persists, [PROVIDER_OUTAGE.md](PROVIDER_OUTAGE.md). |
| `REJECTED` | The provider says the purchase is not valid. | Ask the seller for their provider-side receipt. Usually a cancelled or refunded purchase. |
| `WRONG_TENANT` | The token is already bound to another shop. | **Investigate before helping.** Either a seller with two shops used the wrong one, or someone is replaying a purchase. Both are in `billing.replay_blocked`. |
| `UNKNOWN_PRODUCT` | The product id maps to no plan. | Our configuration. Add the mapping to `PLAY_PRODUCT_PLAN_MAP` and ask the seller to restore purchases. |
| `WRONG_PACKAGE` | The purchase belongs to a different app. | Not our purchase. Do not grant anything. |

---

## Immediate containment

**A paying seller must not be blocked while this is investigated.** Issue
support credit — it is audited, it is time-boxed, and it never auto-renews:

```bash
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
  "$API/v1/admin/repairs/grant_support_credit" \
  -d '{"reason":"Play returned UNAVAILABLE for 3 hours; seller has a receipt",
       "tenant_id":"<tenant>",
       "params":{"plan":"pro","days":14}}'
```

Requires `BILLING_GRANT`, which only `SUPERADMIN` holds — deliberately, because
it is the one repair that gives away revenue.

---

## Recovery

### The provider was unreachable

1. Ask the seller to open the app and use **Restore purchases**. It re-runs the
   full verification, including the wrong-tenant refusal, so it can never grant
   more than a fresh purchase would.
2. Or run reconciliation server-side:

   ```bash
   curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
     "$API/v1/admin/repairs/reconcile_subscription" \
     -d '{"reason":"post-outage catch-up","tenant_id":"<tenant>"}'
   ```

   An `UNAVAILABLE` result means nothing changed, which is correct: local state
   is never revoked because a provider did not answer.

### The notification was missed

`reconcile_billing` runs twice a day and catches this on its own. To force it
for one shop, use `reconcile_subscription` as above.

### The seller cancelled and was charged anyway

Not ours to fix. Play refunds are issued in the Play Console and bKash refunds
through the merchant portal. Record the outcome on a support case; when the
refund lands, the provider's notification moves the subscription to `REFUNDED`
and access ends then.

---

## Seller impact

- They are on Free until this resolves. Their **data is untouched** — section 51,
  and there is a test for each of orders, customers, payouts, cases and profit
  history staying readable on a lapsed plan.
- What they lose is automation and volume, not records.
- `GET /v1/billing/history` shows them the refused attempt, so they can see the
  payment was tried rather than concluding the money vanished.

---

## Verification

- `GET /v1/admin/tenants/{id}` shows the expected plan and status.
- `GET /v1/billing/entitlements` as the seller returns the paid plan.
- Exactly one `subscription_events` row for the change, with a reason.
- If credit was issued: `grant_reason` is on the subscription and there is a
  `billing.manual_grant` audit entry naming the operator.

---

## What never to do

- **Do not edit `subscriptions` directly.** Nothing explains it afterwards, and
  the next reconciliation run will overwrite it with provider truth anyway.
- **Do not extend a period to "match" a provider you have not asked.** Ask.
- **Do not use the manual provider as a workaround for a broken Play
  integration.** It is support credit with an expiry and a reason, and it should
  look like that in the record.
