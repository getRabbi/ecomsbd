# Runbook — reconciliation error

**Master spec sections 16, 81, 82, 112.** Reconciliation decides which payout
line paid for which parcel. Getting it wrong is worse than not doing it: a
missed match costs a click, a **wrong** match tells a seller they have been paid
when they have not.

Section 112's rule governs every decision here: **precision before recall.**

---

## Detection

| Signal | Where |
|---|---|
| `reconciliation_cases_open` climbing | `GET /v1/admin/ops/counts` |
| Unmatched payout amount rising | dashboards |
| A seller says a settled parcel is unpaid | support — treat as P1 |
| Receivables and the ledger disagree | `rebuild_money_summary`, `difference_paisa != 0` |

---

## Triage: which kind is it?

```sql
SELECT kind, status, COUNT(*), SUM(amount_paisa) AS paisa
  FROM reconciliation_cases
 WHERE status = 'OPEN'
 GROUP BY kind, status
 ORDER BY paisa DESC;
```

| Kind | Meaning | Usually |
|---|---|---|
| `DELIVERED_UNPAID` | Delivered, money never arrived | The courier's settlement lag. Not a bug. |
| `UNDERPAID` | Money arrived short | A deduction we did not classify. Read the provider's own label on the adjustment. |
| `OVERPAID` / `UNEXPECTED_PAYMENT` | More than owed, or for a parcel we have no record of | Often a duplicate booking — see [DUPLICATE_BOOKING.md](DUPLICATE_BOOKING.md). |
| `AMBIGUOUS_MATCH` | Two parcels share a reference | The engine refused on purpose. A person decides. |
| `STALE_IN_TRANSIT` | Parcel has not moved | A courier problem, not a money problem. |

**A high case count is not automatically a bug.** The cases exist so a seller
sees what is unresolved. Escalate on the *rate of change*, not the total.

---

## Immediate containment

If a **matching rule** is suspected of settling things wrongly:

1. Turn auto-match off. Suggestions keep flowing; nothing settles itself.

   ```bash
   curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
     "$API/v1/admin/repairs/disable_provider_capability" \
     -d '{"reason":"investigating false matches on the reference rule",
          "params":{"flag":"reconciliation_auto_match","enabled":false}}'
   ```

2. Re-run the affected payout in **shadow mode**. It runs the whole engine and
   writes nothing (section 112), so it is safe on live data:

   ```
   POST /v1/reconciliation/payouts/{payout_id}/reconcile?shadow=true
   ```

   Compare its proposed matches with what was actually settled. That difference
   is the bug.

---

## Data queries

```sql
-- Money that arrived and has not been applied to anything.
SELECT p.id, p.provider, p.paid_on, p.total_paisa, p.applied_paisa,
       p.total_paisa - p.applied_paisa AS unapplied
  FROM payouts p
 WHERE p.total_paisa <> p.applied_paisa
 ORDER BY unapplied DESC;

-- Lines the engine refused, with the reason it refused.
SELECT id, payout_id, amount_paisa, status, confidence,
       provider_consignment_id, merchant_reference
  FROM payout_lines
 WHERE status IN ('UNMATCHED', 'AMBIGUOUS', 'UNMAPPABLE')
 ORDER BY amount_paisa DESC;

-- The invariant, per shop.
SELECT r.tenant_id,
       SUM(GREATEST(0, r.collectible_paisa + r.adjustment_paisa
                       - r.settled_paisa - r.deduction_paisa)) AS outstanding
  FROM cod_receivables r
 WHERE r.status IN ('COLLECTIBLE','PARTIALLY_SETTLED','MISMATCHED','DISPUTED')
 GROUP BY r.tenant_id;
```

---

## Recovery

### A wrong match was applied

1. `reverse_reconciliation_match` on the payout line. Section 81.8: a reversal,
   never a deletion. The original ledger entries stay and two more undo them, so
   the mistake and its correction are both readable a year later.
2. Re-match by hand with a stated reason, or leave it for the seller.
3. Confirm `difference_paisa = 0` with `rebuild_money_summary`.

### The statement was mis-parsed

1. The source file is always retained (section 81.4), so re-parse it:
   `rerun_payout_parser` with the source file id. It reports what the parser now
   makes of the file and applies nothing.
2. If the column mapping was wrong, the seller re-imports with a corrected
   mapping. The duplicate-content check refuses the identical bytes, which is
   correct — a corrected mapping is a new decision, not a re-upload.

### Never do this

- Do not edit `settled_paisa` directly. It is guarded by a database check
  constraint, and an edit leaves no trace of who decided it.
- Do not close a case without a note. The endpoint refuses, and the reason is
  that next month's identical case needs to know what was done this month.

---

## Seller impact

- Their outstanding figure is too high or too low until this is resolved. Say
  which, and by how much. "There is an issue" is not an answer a seller can use.
- Money already correctly settled is unaffected: reversals are scoped to the
  lines involved.
- The Money screen keeps working throughout. Cases are shown as cases, which is
  the design (section 16) — the seller sees the unresolved thing rather than a
  confidently wrong total.

---

## Verification

- `difference_paisa = 0` for every affected shop.
- The open case count stops growing.
- A shadow run over the last three payouts proposes the same matches that were
  actually applied.
- Before re-enabling auto-match: shadow-run a week of payouts and confirm
  **zero** false matches. Section 112 asks for high precision before the
  threshold goes back on, not for a plausible-looking sample.
