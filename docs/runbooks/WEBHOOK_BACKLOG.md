# Runbook — webhook backlog

**Master spec sections 40, 41, 49, 78.** Webhooks carry courier status and
billing events. A backlog means the product's picture of the world is stale:
parcels look in transit that have been delivered, and subscriptions look active
that have been cancelled.

---

## Detection

| Signal | Where |
|---|---|
| `billing_webhook_failures` climbing | `GET /v1/admin/ops/counts` |
| `outbox_dead_letters` above zero | same |
| Webhook lag (`received_at − occurred_at`) rising | dashboards |
| Provider console shows retries | provider dashboard |

---

## Triage

```sql
-- Billing webhooks that did not process.
SELECT provider, state, dedupe_source, COUNT(*), MAX(received_at)
  FROM billing_webhook_events
 WHERE state IN ('FAILED', 'REJECTED')
 GROUP BY provider, state, dedupe_source;

-- How far behind we are, where the provider stamps its own event time.
SELECT provider,
       AVG(EXTRACT(EPOCH FROM (received_at - occurred_at))) AS avg_lag_s,
       MAX(EXTRACT(EPOCH FROM (received_at - occurred_at))) AS worst_lag_s
  FROM billing_webhook_events
 WHERE occurred_at IS NOT NULL
   AND received_at > now() - interval '6 hours'
 GROUP BY provider;

-- Outbox work that has stopped moving.
SELECT topic, status, COUNT(*), MIN(available_at) AS oldest
  FROM outbox_events
 WHERE status IN ('PENDING','FAILED','DEAD')
 GROUP BY topic, status
 ORDER BY 3 DESC;
```

**`REJECTED` is not a backlog.** It means signature verification failed, which
is either a misconfigured secret or someone probing the endpoint. Check
`signature_verified = false` and go to the last section.

---

## Immediate containment

1. **Is the worker running at all?**

   ```bash
   docker compose ps worker
   docker compose logs --tail=100 worker
   ```

   A stopped worker is the most common cause and the easiest fix.

2. **Is Redis reachable?** ARQ needs it, and `/health/ready` reports it.

3. **Is the ingress endpoint erroring?** A provider that receives a 500 retries;
   one that receives a 401 may give up. Check that first — a provider giving up
   loses events permanently, and that is unrecoverable without reconciliation.

---

## Recovery

Webhook processing is idempotent by construction, so replay is safe:

- **Billing** — `replay_billing_webhook` per stored event. It only replays
  events that already passed signature verification; an unverified body is
  never re-trusted, whatever an operator asks for.

  ```bash
  curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
    "$API/v1/admin/repairs/replay_billing_webhook" \
    -d '{"reason":"worker down 14:00-15:30, replaying missed renewals",
         "target_type":"billing_webhook","target_id":"<event-id>"}'
  ```

- **Outbox** — the dispatcher drains automatically every 15 seconds once the
  worker is back. Dead-lettered events need `retry_outbox_event` individually,
  deliberately: an event that has failed twelve times deserves a look rather
  than a thirteenth attempt.

- **Events lost entirely** — `reconcile_billing` re-asks the provider what each
  subscription's real state is. Section 90 provides it for exactly this, and it
  is authoritative where a replayed webhook is only a copy.

Deduplication (section 78) means a provider redelivering everything costs
nothing: the second delivery increments a counter and applies nothing, and a
replayed renewal sets the same absolute expiry, so it cannot buy a second month.

---

## Seller impact

| Backlog | What the seller sees |
|---|---|
| Courier status | Parcels stale in their last known state. Alerts still fire from the shop's own data, so "delivered but unpaid" still surfaces. |
| Billing renewal | Nothing. Access runs to `current_period_end` regardless of when we hear about the renewal. |
| Billing cancellation | A subscription that looks active slightly longer than it is. Reconciliation corrects it, and nothing was charged. |
| Billing failure | Grace does not start until we know. The dunning window is measured from when we heard, which is the honest starting point. |

---

## Verification

- `billing_webhook_failures` and `outbox_dead_letters` return to zero.
- Lag returns to seconds.
- Spot-check one replayed event: the subscription state matches the provider,
  and there is exactly one `subscription_events` row for it.
- `SELECT COUNT(*) FROM billing_webhook_events WHERE delivery_count > 1` — a
  healthy number here is *good*. It means deduplication is absorbing retries.

---

## If the signature is failing

Not a backlog. Either the shared secret is wrong, or someone is posting to the
endpoint.

1. Check the configured secret against the provider console.
2. If it is correct, treat the traffic as hostile. The endpoint already refuses
   and records every attempt, so there is no exposure — but the source is worth
   knowing.
3. **Never** disable verification to clear a backlog. An unsigned billing
   webhook endpoint is a way for anyone to grant themselves a subscription.
