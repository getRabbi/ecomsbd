# V3.7 External Risk + Network Intelligence

Migration `a37001`. Code: `backend/app/risk_providers/`, `backend/app/analytics/network*.py`,
`backend/app/analytics/courier_intelligence.py`.

## External risk providers

- **Provider-neutral layer.** `app/risk_providers/contract.py` defines the adapter port
  (`test_connection`, `lookup`), typed failures and the allow-listed fact codes.
  `registry.py` is **empty**: no licensed, documented provider contract exists yet, so every
  shop sees `GATED` / `EXTERNAL_RISK_PROVIDER_REQUIRED`. Adding a provider means adding a
  reviewed adapter for a real contract; environment switches cannot add one.
- **Connection** (`risk_provider_connections`): credentials AES-GCM encrypted, bound to shop
  and provider, never returned (only `credentials_set` and the last four characters). New
  credentials disable the provider until a connection test passes. Health is
  `UNKNOWN / HEALTHY / DEGRADED / DOWN`; three failures in a row, or an auth failure, is DOWN
  and pauses calls for 5 minutes. A provider `429` pauses calls until its `retry_after`.
- **Lookups** (`external_risk_lookups`): one row per provider call, which is also the audit
  trail (plus an `external_risk.lookup` audit entry). Only the phone in E.164 and the market go
  out. Answers are cached for the connection's TTL (provider-bounded); a refresh within 10
  minutes returns the stored answer; 200 provider calls per shop per day. Timeouts and
  unavailability retry once; auth, rejection and rate limits do not. Rows older than 180 days
  are pruned daily (`prune_external_risk_lookups`, 04:20 UTC).
- **Separation.** `GET /customers/{id}/risk-profile` returns `own_shop` (first-party Risk
  Check, unchanged), `external` (provider facts with provider name, provider timestamp,
  freshness, sample/confidence only when supplied) and `network` (anonymous cohort context).
  Provider facts never change the Risk Check band. Opaque scores are dropped at
  normalization, and nothing decides an order automatically.

| Action | Permission |
| --- | --- |
| Provider settings, test, enable, remove | `settings.manage` (owner) |
| Read stored provider facts, risk profile | `customer.risk_view` |
| Ask a provider (lookup / refresh) | `customer.external_risk_lookup` (owner, manager) |

## Network intelligence

- **Central thresholds** in `app/analytics/network_privacy.py`: at least 20 shops, 200
  observations, 10 per shop, clipping at 100 per shop, no shop above 10% of the clipped total,
  both outcomes present. Rates are rounded to 5%, durations and charges to fixed steps, and
  samples shown only as bands. Otherwise the cell is `DATA_NOT_SUFFICIENT`.
- **Cells** (`network_benchmark_cells`) are written once per closed month by the existing
  `build_network_benchmarks` job, idempotently, for a fixed set of cohorts, so a cohort's
  presence says nothing. The metrics are RTO rate, delivery success, booking→delivery hours,
  stuck parcels (>10 days), delivery→payout days, reconciliation discrepancy rate and courier
  charge. The cohorts are all shops, courier (Pathao, Steadfast), broad category and monthly
  volume band. Contributors are opted-in, active, single-owner shops, grouped by owner.
- **Endpoints:** `GET /network-intelligence` (headline plus all-shop cells),
  `GET /network-intelligence/benchmarks?dimension=` (drilldown with the cohort definition and
  freshness), and `GET /network-intelligence/couriers` (the shop's own factual per-courier
  metrics for 90 days beside the courier cohorts). There is no score, ranking or "best
  courier".

## Automation

- Triggers: `risk.state_changed` and `risk.repeated_rto` (from a parcel's status change,
  first-party), `risk.external_lookup_completed`, `risk.external_provider_unavailable`.
- Conditions: `risk_state`, `previous_risk_state`, `external_data_state`
  (FRESH/STALE/NONE), `external_found`, `network_rto_percent`.
- New action `HOLD_FOR_REVIEW`: marks the order `review_hold`. Workflows then skip
  `BOOK_COURIER` and `PUSH_STORE_STATUS` for it until a person releases it
  (`DELETE /orders/{id}/review-hold`). It never cancels anything or touches money or stock.

## Screens

- **Web:** Settings → Risk provider; the customer page has separate own-shop, provider and
  network sections; `/network` has Overview, Cohorts and Couriers tabs plus a data-quality
  explanation; the order drawer shows a review hold with a release button.
- **Mobile:** the customer and Risk Check screens show provider facts, freshness,
  unavailable state, network context and refresh (owner and manager only).
