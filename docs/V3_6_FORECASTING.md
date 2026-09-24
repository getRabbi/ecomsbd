# V3.6 Forecasting & Advanced Intelligence

Migration `a36001`. Backend code is in `backend/app/forecasting/` and the API is at `/v1/forecasting/*`.

- **Web:** `/forecasting` has three tabs: Reorder, Accuracy and Cash outlook. There is also an item page with a daily chart.
- **Mobile:** Settings → Forecasts shows items likely to run out, the suggested reorder and the cash outlook.

Everything here is a deterministic, explainable estimate calculated from the shop's own ledgers. There is no black-box model and no external data. Nothing is ever ordered automatically. The screens call every figure a forecast, never a guarantee.

## Demand forecast (`app/forecasting/engine.py`)

- **Demand.** Units booked out, net of cancellations (`BOOKED_DECREMENT` − `CANCEL_RESTORE`). This is the same count the inventory insights use.
- **History.** The last 56 **completed** days, which ends yesterday. A day still in progress would look like a slow day.
- **In-stock days only.** A day counts if the item existed, started the day with stock, or sold on it. Days when it was out of stock, and days before the item was created, are left out rather than counted as zero demand.
- **Rate.** Half the recent average plus half the long average:
  - the recent average covers the last 14 days, used when at least 7 of them were in stock;
  - the long average covers the whole window.
- **Safety stock.** `ceil(1.65 × σ_daily × √lead_time)`, which is about a 95% cycle service level.
- **Reorder point.** `ceil(rate × lead_time) + safety`.
- **Suggested quantity.** `ceil(rate × (lead_time + cover_days)) + safety − on_hand − on_order`, and never below 0. `cover_days` defaults to 14; web offers 7–60 and the API accepts 1–120.
- **At risk.** The rate is above zero, on hand plus on order is at or below the reorder point, and the suggested quantity is above zero.
- **Confidence.**
  - `INSUFFICIENT`: fewer than 14 in-stock days or fewer than 5 units. No numbers are shown at all.
  - `HIGH`: at least 28 in-stock days and at least 20 units.
  - `MEDIUM`: everything else.

## Lead time

The lead time comes from the item's preferred supplier. The first rule that applies wins:

1. **Observed:** the median of ordered → first received days over the supplier's last 10 received POs. Needs at least 3 of them.
2. **Supplier:** the seller's own estimate, `suppliers.lead_time_days` (new column, set in the supplier form).
3. **Default:** 7 days, shown as "assumed".

## Snapshots, accuracy, alerts and automation

- **Daily snapshot.** The ARQ job `snapshot_demand_forecasts` runs at 02:50 UTC (08:50 Dhaka), before the morning alert scan. It stores one `demand_forecasts` row per item per day. It is idempotent per shop and day, and prunes rows older than 120 days.
- **Accuracy.** Only stored forecasts are scored.
  - The snapshot from day D predicts days D to D+27. Once that horizon has passed, it is compared with actual net sales.
  - The error is WAPE: Σ|predicted − actual| / Σ actual. The response also returns the bias in units.
  - Nothing is shown until at least 10 items can be scored.
- **Smart Alert `STOCKOUT_PREDICTED`.**
  - Reads the latest snapshot, which must be from today or yesterday.
  - Covers at-risk items that are not `INSUFFICIENT`.
  - Goes to people with `procurement.manage`; routes to `forecasting`.
- **Workflow trigger `inventory.stockout_predicted`** (subject: product).
  - Fired through the outbox event `forecast.stockout_predicted`, which has a no-op handler.
  - Fired only when an item becomes at risk compared with the previous snapshot, so an item that stays at risk is announced once.
  - `CREATE_DRAFT_PO` has a new `use_suggested` option. With a suggestion, it uses the forecast's quantity; without one, it falls back to its configured `quantity`.
  - It only ever creates a draft, and the existing de-duplication against draft or open POs still applies.
- **Seller draft.** `POST /forecasting/draft-purchase-order` creates a DRAFT with source `SELLER`. It uses the suggested quantity unless the seller gives another.

## Cash outlook

`GET /forecasting/cash` (`money.view`) compares money in and out:

- **In:** the money core's existing COD forecast windows, unchanged.
- **Out:** supplier payables from purchase orders (received value − paid), grouped by due date: overdue, next 7 days, days 8–14, later, and no date.
- **Committed:** open orders not yet received are shown separately as "committed". They are not owed until received.
- **Net:** figures for 7 and 14 days.

It never touches the financial ledger and is not a bank balance.

## Permissions

| Action | Permission |
| --- | --- |
| Forecasts, reorder suggestions, accuracy | `product.view` |
| Draft a PO from a suggestion | `procurement.manage` |
| Cash outlook | `money.view` |

## Deliberately not built

- Seasonality or weekday profiles, and ML models: not enough history per SME item to fit them honestly.
- External market signals.
- Automatic ordering.
- Per-location forecasts.
- Promotion or price-elasticity effects.
