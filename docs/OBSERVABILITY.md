# Observability

**Master spec section 49.** What is measured, where it comes from, and what
number means "wake someone up".

One rule governs every metric here: **no customer PII in a label.** A metric
labelled by phone number is a phone-number dataset with a graph on top. Tenant
id is acceptable (it is an opaque UUID and support needs it); anything that
identifies a person is not.

---

## Where the numbers come from today

| Source | Status |
|---|---|
| Structured JSON logs with `trace_id`, `tenant_id`, `actor_type`, redacted | **Working.** Every request and job. |
| `GET /v1/admin/ops/counts` | **Working.** The failure queues, computed on demand. |
| `GET /v1/admin/ops/provider-health` | **Working.** Per provider and capability. |
| `GET /health/live`, `GET /health/ready` | **Working.** Ready checks the database, the migration revision and Redis. |
| Audit log | **Working.** Every sensitive action, queryable by action and tenant. |
| Sentry | **Wired, inert.** `SENTRY_CONFIGURATION_REQUIRED`. |
| Metrics backend (Prometheus/OTel) | **Not provisioned.** The counters below are derivable from the tables named; nothing scrapes them yet. |

That last row is the honest gap. The *data* exists for everything in this
document — each metric names the table it comes from — but there is no scraper
and no dashboard. Wiring one is configuration, not redesign.

---

## Money and operations

| Metric | Source | Watch for |
|---|---|---|
| `booking_unknown_count` | `consignments` where `status = 'BOOKING_UNKNOWN'` | Any sustained rise. See [BOOKING_UNKNOWN_SPIKE.md](runbooks/BOOKING_UNKNOWN_SPIKE.md). |
| `duplicate_booking_incidents` | orders with >1 live consignment | **Any non-zero value is a P0.** |
| `payout_auto_match_precision` | shadow-mode runs vs. applied matches | Below 99% — stop auto-matching. Section 112. |
| `payout_unmatched_amount_paisa` | `payouts.total_paisa − applied_paisa` | Rising week over week. |
| `reconciliation_cases_open` | `reconciliation_cases` where `status='OPEN'` | The *rate of change*, not the total. |
| `receivable_ledger_drift_paisa` | the section 81.10 invariant, per tenant | **Any non-zero value.** This is the number that says a money figure is wrong. |
| `cod_outstanding_paisa` | `cod_receivables` | Business health, not an alert. |

## Courier integration (Steadfast)

Emitted by `app/couriers/metrics.py` as structured log lines **and** kept as
in-process counters, readable through the admin ops endpoint. There is still no
scraper (see the gap above), so these are a diagnostic aid rather than a source
of truth — anything that must survive a deploy is in a table.

The label allowlist is enforced in code, not documented: `record_metric` drops
anything outside it rather than trusting each call site. **No provider status
string is ever a label** — an unbounded provider value would blow up
cardinality, so it lives on the `courier_events` row and in the log line.

| Metric | Source | Watch for |
|---|---|---|
| `steadfast_create_success` | booking | Business volume, not an alert. |
| `steadfast_create_ambiguous` | booking | **The important one.** A rise means bookings are ending unconfirmed, which is orders a seller cannot book. Compare against `steadfast_unknown_recovered`. |
| `steadfast_create_failed` | booking | Grouped by `reason`; a spike on one reason is usually a field the courier started rejecting. |
| `steadfast_duplicate_prevented` | booking guard | **Healthy when non-zero.** Every increment is a duplicate parcel that did not happen. |
| `steadfast_unknown_recovered` | recovery job | Should track `create_ambiguous` closely. A growing gap means bookings are getting stuck. |
| `steadfast_unknown_unresolved` | recovery job | **Any value needs a person.** The attempt budget ran out and the parcel is in manual review. |
| `steadfast_status_sync_lag` | `consignments.next_poll_at` overdue | Measured from the *due* time, so a healthy fleet on a six-hour interval reads zero. Above ~1h means the poll job is not keeping up. |
| `steadfast_status_unknown_value` | `courier_events.status_undocumented` | **Any non-zero value.** Steadfast added a delivery status the mapping table does not know. Query the raw value from `courier_events` and decide what it means before it appears in a report. |
| `steadfast_payment_sync_count` | payment sync | Imports per run. |
| `steadfast_payment_sync_error` | payment sync | Any sustained value; check `courier_provider_payments.sync_state = 'FAILED'`. |
| `steadfast_unmatched_payment_amount` | payout total − applied | Rising week over week means reconciliation is falling behind, same as the statement path. |
| `steadfast_auth_failure` | credential validation | A platform-wide rise means *they* changed something; a single-tenant rise is that shop's key. |
| `steadfast_latency` | client | Count + sum, so a mean is derivable. Doubling against yesterday. |
| `steadfast_return_duplicate_prevented` | return guard | Healthy when non-zero. |
| `steadfast_webhook_not_configured` | webhook receiver | **Rising is informative, not broken.** It means Steadfast *is* sending callbacks, and chasing `STEADFAST_WEBHOOK_CONTRACT_REQUIRED` would be worth someone's time. |

Two provider-health rows also matter, per capability rather than per provider:
a courier whose status lookup is failing can still take bookings, and the
breaker opens on the capability rather than the whole integration.

## Providers

| Metric | Source | Watch for |
|---|---|---|
| `provider_error_rate` | `provider_health.error_count / (success+error)` | Above 5% for 10 minutes. |
| `provider_breaker_open` | `provider_health.breaker_state` | Any open breaker, per provider. |
| `provider_latency_ms` | `provider_health.latency_ms_ema` | Doubling against yesterday. |
| `provider_auth_failures` | `provider_health.auth_failure_count` | A platform-wide rise means *they* changed something; a single-tenant rise is that shop's credentials. |

## Queues and webhooks

| Metric | Source | Watch for |
|---|---|---|
| `webhook_lag_seconds` | `received_at − occurred_at` | p95 above 60s. |
| `webhook_failures` | `billing_webhook_events` `FAILED`/`REJECTED` | Any `REJECTED` — that is a signature failure, not a backlog. |
| `webhook_duplicate_rate` | `delivery_count > 1` | Healthy when non-zero: deduplication absorbing retries. |
| `outbox_backlog` | `outbox_events` `PENDING` | Above 500, or an `available_at` older than 10 minutes. |
| `outbox_dead_letters` | `outbox_events` `DEAD` | Any. Each one needs a person. |

## Billing

| Metric | Source | Watch for |
|---|---|---|
| `subscription_verification_failures` | `billing_transactions` `state='FAILED'` | A rise, grouped by `verification_result` — the value tells you which runbook. |
| `billing_replay_blocked` | audit `billing.replay_blocked` | **Any.** Either a confused seller with two shops or someone replaying a purchase. |
| `subscriptions_active` / `_grace` / `_past_due` | `subscriptions.status` | Grace and past-due rising together means dunning is not recovering. |
| `entitlement_denials` | audit `billing.entitlement_denied` | A spike on one key usually means a plan value is wrong, not that sellers suddenly hit a limit. |
| `subscription_sync_staleness` | `now() − last_synced_at` | Above 36 hours — the reconciler is not running or the provider is unreachable. |

## Auth and notifications

| Metric | Source | Watch for |
|---|---|---|
| `otp_delivery_failures` | `otp_challenges.delivery_status='FAILED'` | Above 2% — a gateway problem, and nobody can sign in. |
| `otp_rate_limit_trips` | audit `security.rate_limit_tripped` | A spike from few IPs is abuse; from many is a bug. |
| `refresh_reuse_detected` | audit `auth.refresh_token_reuse_detected` | **Any.** It means a token was replayed. |
| `notification_delivery_failures` | `notification_deliveries.state='FAILED'` | Above 5%. `NOT_CONFIGURED` is expected today and is a different state on purpose. |
| `sms_segments_used` | `usage_counters` | Cost. Compare `estimated_segments` with `provider_segments` — a persistent gap means a template is mis-estimated. |

## Security

| Metric | Source | Watch for |
|---|---|---|
| `cross_tenant_access_blocked` | audit `security.cross_tenant_access_blocked` | **Any. P0.** The guard did its job, and something tried. |
| `admin_access_denied` | audit `admin.access_denied` | A run of these means someone is guessing admin tokens. |
| `pii_reveals` | audit `admin.pii_revealed`, `privacy.phone_revealed` | Review weekly. Each one has a stated reason; read them. |
| `exports_requested` | audit `data.export_requested` | An unusual volume from one shop before an account deletion is worth a look. |

## Sync and app health

| Metric | Source | Watch for |
|---|---|---|
| `sync_conflicts` | `sync_mutations.status='CONFLICT'` | A rise means two devices are editing the same records, or a client bug. |
| `crash_free_sessions` | Sentry (mobile) | Below 99.5%. |
| `api_error_rate` / `api_latency_p95` | access logs | Section 105's targets: Home and order list p95 under 500 ms. |

---

## Alert thresholds worth waking someone for

Everything else is a dashboard.

1. `receivable_ledger_drift_paisa != 0` — a money figure is wrong.
2. `duplicate_booking_incidents > 0` — a seller is being charged twice.
3. `cross_tenant_access_blocked > 0` — isolation was tested by something.
4. `refresh_reuse_detected > 0` — a session token was replayed.
5. `/health/ready` failing — the API cannot serve.
6. `otp_delivery_failures > 10%` — nobody can sign in.
7. `outbox_dead_letters` growing — work is being silently dropped.

---

## What is deliberately not measured

- **Anything keyed by customer phone, name or address.** Not in a metric label,
  not in a log line, not in a Sentry event. Redaction is applied centrally in
  the formatter, so this holds for modules written later too.
- **Per-seller revenue in a shared dashboard.** It is their business, and an
  operations dashboard is not where it belongs.
- **A "provider up" boolean from a single request.** Health is a pattern:
  five consecutive failures, not one timeout.
