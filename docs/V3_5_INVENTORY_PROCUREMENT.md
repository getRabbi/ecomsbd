# V3.5 Inventory + Procurement

Migration `a35001`. Backend in `backend/app/procurement/`, API at `/v1/procurement/*`.
Web at `/procurement`; mobile under Settings → Purchasing.

## Suppliers

- Name, contact name, phone, email, address, notes, payment terms (days), and active or inactive.
  Names are unique per shop, ignoring case.
- Supplier items link a product or variant to a supplier, with the last unit cost and one
  preferred supplier per item.

## Purchase orders

- `DRAFT → ORDERED → PARTIALLY_RECEIVED → RECEIVED`. `DRAFT` and open orders can also be
  `CANCELLED`, and a cancel needs a reason.
- Only a `DRAFT` can be edited. Edits carry a `version`, and a stale version is refused.
- Numbers are `PO-00001`, allocated under the per-shop lock.
- The `source` field records `SELLER` or `AUTOMATION`. Automation only ever creates a `DRAFT`, and a
  person orders it.

## Receiving

- A receipt can be partial, and there can be many of them. Each line records accepted units and
  rejected units, with a reason (`DAMAGED`, `WRONG_ITEM`, `EXPIRED`, `OTHER`).
- A receipt is idempotent by `idempotency_key`:
  - the same key with the same request replays the first receipt;
  - the same key with a different request returns `409`.
- Receipts on one shop are serialized under the shop lock.
  The PostgreSQL test sends three concurrent receipts and gets one success and two refusals.
- Over-receipt is refused (`409 OVER_RECEIPT`) unless someone with `procurement.manage` gives a
  reason. That receipt is audited as `GOODS_OVER_RECEIVED`.
- Accepted units become a `RESTOCK` movement with source `PURCHASE`, the PO number as the reference, and
  key `po-receipt:{receipt}:{line}`. The movement goes through `StockService.record_movement`, the
  only ledger writer. Storefront sync and low-stock triggers see it like any restock.
- Rejected units never enter stock.

## Supplier cost rule

- Rule: **when a line has `update_cost` set, its received unit cost becomes the item's current
  cost.** Nothing is averaged.
- Orders already placed keep their `unit_cost_snapshot_paisa`, so historical profit never changes.
- With `update_cost` off, the current cost is left alone.

## Payables

- Payables live on the purchase order: `total`, `received_value`, `paid`, and `payment_due_at`
  (set on the PO, or from the supplier's terms counted from the first receipt). Supplier payments are rows in
  `supplier_payments`. They are idempotent by key.
- A payment cannot exceed:
  - the received value, once the order is `RECEIVED` or `CANCELLED`;
  - otherwise the larger of the total and the received value. This allows an advance.
- Payables are kept apart from COD and the financial ledger (no `LedgerEntry`). Access needs
  `money.view` to see payables and `payable.manage` to pay (Finance and Owner).

## Stock states

- On hand (the ledger), low (below the product threshold), incoming (open POs:
  ordered − received − rejected), per-location quantities, damaged in the last 30 days, and rejected
  on receipt in the last 30 days.
- Reservations are **not** shown. The API returns `reservations_tracked: false` rather than a made-up
  number.

## Locations and transfers

- Every shop has a derived default location (`MAIN`). It is created lazily, and single-location
  shops behave exactly as before.
- Extra locations hold `warehouse_stock` rows (quantity ≥ 0). The default location's
  quantity is the on-hand total minus the extra locations.
- A transfer is checked against available stock (for the default location, that means minus allocated
  units). It writes a paired `TRANSFER_OUT` / `TRANSFER_IN` movement with keys `{key}:out` and
  `{key}:in`. The pair is idempotent, and the total does not change. For that reason it emits no inventory-sync
  event.
- A location that still holds stock cannot be closed.
- Deferred: batch, expiry and serial numbers; per-location storefront sync.

## Alerts and automation

- Smart Alerts: `PURCHASE_ORDER_OVERDUE` and `PARTIAL_RECEIPT_PENDING` (3 days) go to
  `procurement.manage`. `SUPPLIER_PAYMENT_OVERDUE` goes to `payable.manage`.
- Workflow triggers:
  - `purchase_order.ordered`, `.partially_received` and `.received`;
  - `purchase_order.overdue`, `supplier_payment.due` and `transfer.completed`.
- Actions:
  - `CREATE_TEAM_TASK`;
  - `CREATE_DRAFT_PO` — uses the preferred supplier and does nothing if an order is already open for the item. The
    publisher needs `procurement.manage`. It never orders anything by itself.
- Storefront integrations receive receipt stock through the existing `inventory.updated` event.
  There is no separate supplier sync.

## Permissions

| Action | Permission |
| --- | --- |
| See suppliers, POs, stock | `product.view` |
| Manage suppliers, POs, locations, transfers, over-receipt | `procurement.manage` (Manager, Owner) |
| Receive goods | `inventory.adjust` |
| See payables | `money.view` |
| Record supplier payments | `payable.manage` (Finance, Owner) |
