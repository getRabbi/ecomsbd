# Runbook — duplicate booking

**Master spec sections 11, 36, 77.** A parcel booked twice means the customer
gets two deliveries, the seller pays two delivery charges, and — worst — one
consignment settles while the other returns, so the money never reconciles.

This is a **P0**. It costs the seller money directly and it costs them their
trust in the figures, which is the product.

---

## Detection

| Signal | Where |
|---|---|
| Two consignments for one order | query below |
| Courier invoice shows more parcels than the shop dispatched | seller |
| A reconciliation case of kind `UNEXPECTED_PAYMENT` | `GET /v1/reconciliation/cases` |
| `duplicate_booking_incidents` metric above zero | dashboards |

---

## Immediate containment

1. **Stop the source.** If a client retry loop is creating them, disable that
   courier's flag now. One more duplicate is worse than an hour of manual mode.

2. Find every affected parcel before touching any of them. Fixing one at a time
   while more are created is how an incident lasts a day.

```sql
-- Orders with more than one live consignment. The definition of the bug.
SELECT order_id, tenant_id, COUNT(*) AS consignments,
       array_agg(id) AS consignment_ids,
       array_agg(provider_consignment_id) AS provider_ids
  FROM consignments
 WHERE status NOT IN ('CANCELLED', 'BOOKING_FAILED')
 GROUP BY order_id, tenant_id
HAVING COUNT(*) > 1;
```

---

## How it happens

Three causes, three different fixes:

| Cause | Fix |
|---|---|
| Client retried without an `Idempotency-Key` | The endpoint requires one (section 77). A missing key is a client bug — find the build. |
| Retry after a `BOOKING_UNKNOWN` that was in fact created | Follow [BOOKING_UNKNOWN_SPIKE.md](BOOKING_UNKNOWN_SPIKE.md). Never rebook without asking the provider. |
| Provider created two from one request | Their bug. Collect both consignment ids and raise it with them; keep the evidence on a support case. |

---

## Recovery

1. **Cancel the extra parcel with the courier first**, if it has not shipped.
   Doing this before touching our records means our records never claim
   something the courier has not agreed to.
2. Mark the extra consignment `CANCELLED` with a reason naming the duplicate.
3. **Do not delete anything.** Section 80: the ledger is append-only. If the
   duplicate produced ledger entries, reverse them — two compensating rows, so
   both the mistake and its correction stay readable.
4. If a payout already settled against the duplicate, use
   `reverse_reconciliation_match`. It writes the reversal; it does not erase.
5. Open a support case linked to both consignments and to the order, and tell
   the seller what happened and what it cost them. They will see the courier
   charge either way; hearing it from us first is the difference between a bug
   and a betrayal.

---

## Verification

- The duplicate query returns nothing.
- Receivables and the ledger agree for every affected shop:

  ```bash
  curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
    "$API/v1/admin/repairs/rebuild_money_summary" \
    -d '{"reason":"post-duplicate-booking check","tenant_id":"<id>"}'
  ```

  `difference_paisa` must be `0`.
- No new duplicates for 24 hours before the flag goes back on.
