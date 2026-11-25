# Daily launch metrics (5 minutes, every morning)

Source: first-party product milestones (`product_events_enabled`, on for every shop since
2026-11-25 01:00 BDT). One row per shop per milestone, no customer data. Admin header
`X-Admin-Token`.

## 1. Funnel (the four numbers)

```
GET https://api.ecomsbd.com/v1/admin/ops/funnel?from=<YYYY-MM-DD>&to=<YYYY-MM-DD>
```

Read `steps[].shops` for these, for yesterday and for the launch to date (`from=2026-11-25`):

| Step | Event | Healthy in week 1 |
|---|---|---|
| Signed up | `shop_created` | grows daily |
| First order | `first_order` | ≥ 60 % of new shops within 2 days |
| First booking | `first_booking` | ≥ 40 % of shops with a first order |
| First confirmation | `first_confirmation_call` | rising as shops use the call queue |

Also in the answer: `first_courier_connected`, `first_capture` (paste / share / phone / extension),
`first_payout_import`, `first_reconcile`, `weekly_active`. Conversion = shops at a step ÷ shops at
the step before. A step with 0 for two days in a row while the one before grows = a broken path:
open `SUPPORT_PLAYBOOK.md`.

## 2. Health (same morning check)

| Call | Look at | Alarm |
|---|---|---|
| `GET /health/ready` on `api.ecomsbd.com` and `api.scalemyprints.com` | 200, `migration: a48001` | anything else |
| `GET /v1/admin/ops/counts` | outbox / notification / webhook failure counts | rising two days running |
| `GET /v1/admin/ops/provider-health` | Steadfast (and Pathao / RedX) state | `DEGRADED` / `DOWN` |
| `GET /v1/admin/sms/providers` | Alpha SMS account + health | failures; any `UNKNOWN` attempts |
| Northflank logs (`ecomsbd-worker`) | `poll_courier_statuses` `errors` | > 0 for a day |

## 3. Weekly (Friday)

- New shops, first orders, first bookings for the week (funnel with `from` = Monday).
- SMS spent: Alpha SMS portal balance (the API reports no per-message price; segments are recorded).
- Gate evidence to write in `docs/build/PROGRESS.md` → Gates: G1 capture precision
  (Create ÷ (Create + Ignore)), G2 share of new orders with a recorded confirmation.
