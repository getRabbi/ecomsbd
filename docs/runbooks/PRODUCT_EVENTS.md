# Runbook: product funnel events and mobile crash reporting (V4 WP0.2)

Two independent pieces:

- **Funnel events.** The backend writes one `product_events` row when a shop
  reaches a step: `shop_created`, `first_order`, `first_courier_connected`,
  `first_booking`, `first_payout_import`, `first_reconcile`, `plan_paid`, and
  `weekly_active` (once per ISO week, Asia/Dhaka). Rows hold no personal data:
  `props` keeps only integers, booleans and short codes such as a provider name.
  `first_capture`, `first_confirmation_call` and `first_autopilot_order` are
  defined but are emitted only by later V4 work packages.
- **Crash reporting.** The Flutter app starts Sentry only when
  `ECOMSBD_SENTRY_DSN` is supplied at build time. `sendDefaultPii` is off,
  phone numbers are masked with the same `maskPhone` helper as the UI, and
  breadcrumbs that could hold message text are dropped
  (`lib/core/crash_reporting.dart`).

## Flag

| Switch | Where | Default |
|---|---|---|
| `product_events_enabled` | `feature_flags` row (global or per shop), admin console | **off** |
| `ECOMSBD_SENTRY_DSN` | Flutter `--dart-define-from-file` (`env/production.json`) | empty: Sentry off |

The flag only stops new rows. It never deletes existing ones.

## Detection

- `GET /v1/admin/ops/funnel?from=YYYY-MM-DD&to=YYYY-MM-DD` (admin token, `OPS_READ`)
  returns distinct shops per step for Asia/Dhaka days `[from, to]`, at most 366 days.
  All zeros while the flag is on means emission is broken. A step that suddenly
  drops to zero means its source service changed.
- Logs: `product event not recorded` (WARNING) is a swallowed database error.
  `product event for another tenant refused` (ERROR) is a caller passing the
  wrong shop and needs a code fix. Neither log line carries props or personal data.
- Sentry: an unexpected spike in events from one app version.

## Containment

- Funnel: turn `product_events_enabled` off. Seller actions never depend on
  this table, and recording runs in a savepoint that cannot fail the caller, so
  turning it off changes nothing a seller sees.
- Crash reporting: ship a build with an empty `ECOMSBD_SENTRY_DSN`, or disable
  the DSN's client key in Sentry (this takes effect immediately, with no release).

## Recovery

- Missed funnel rows are not backfilled automatically. The source tables
  (`orders`, `consignments`, `payouts`, `billing_transactions`) are the truth.
  A one-off backfill is a reviewed script, never an edit of the ledgers.
- Suspected personal data in Sentry: delete the affected events in Sentry, add a
  test case to `test/core/crash_reporting_test.dart` for the leaking shape, fix
  `scrubEvent`/`scrubBreadcrumb`, and release.

## Verification

- Backend: `python -m pytest tests/test_product_events.py tests/test_product_events_emits.py -q`.
- Mobile: `flutter test test/core/crash_reporting_test.dart`.
- With the flag on in a staging shop, create a shop and an order, then confirm
  `shop_created`, `first_order` and `weekly_active` each count 1 in the funnel.
