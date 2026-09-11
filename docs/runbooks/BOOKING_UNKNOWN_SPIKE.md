# Runbook — booking-unknown spike

**Master spec sections 11, 36, 46.** `BOOKING_UNKNOWN` is the state a parcel
enters when we sent a create request to a courier and **do not know whether it
worked**. A timeout after the provider already created the consignment looks
exactly like a timeout before it did.

The rule this runbook exists to protect: **never rebook automatically.**
Rebooking a parcel that was in fact created gives the customer two deliveries
and the seller two charges, and the seller finds out from the courier's invoice.

> **Live as of Phase C (2026-09-11).** Steadfast booking is implemented, so
> this state is reachable. The automatic half of the procedure below now runs
> as a job: `recover_unknown_bookings` asks Steadfast about the parcel's invoice
> every few minutes on exponential backoff, and promotes it to `BOOKED` the
> moment the provider confirms the parcel exists.
>
> What the job **cannot** do is prove absence. Steadfast's V1 documentation
> describes no "not found" response, so a 404 is not evidence that the parcel
> was never created. After the attempt budget runs out the booking moves to
> `MANUAL_REVIEW` and lands on your desk — which is what the rest of this
> runbook is for.

---

## Detection

| Signal | Where |
|---|---|
| `booking_unknown` count rising | `GET /v1/admin/ops/counts` |
| `steadfast_create_ambiguous` rising faster than `steadfast_unknown_recovered` | courier metrics — recovery is falling behind |
| `steadfast_unknown_unresolved` non-zero | **any value needs a person**: the attempt budget ran out |
| Consignments stuck in the state | query below |
| Sellers reporting "it says booking unconfirmed" | support |

A handful is normal — networks time out. A **spike** means the provider is
slow enough that our timeout is firing before their write completes, and that
is a different problem from an outage.

---

## Immediate containment

1. **Do not rebook.** Not by hand, not with a script. The app offers no
   retry affordance on this state for the same reason.
2. Check whether the provider is degraded at all
   ([PROVIDER_OUTAGE.md](PROVIDER_OUTAGE.md)). A spike with healthy latency
   means our timeout is too aggressive; a spike with rising latency means
   theirs is.
3. If bookings are failing faster than the reconciler resolves them, turn the
   courier off with its flag. Sellers fall back to manual dispatch, which
   works and is honest, rather than accumulating unknowns.

---

## Data queries

```sql
-- The queue, oldest first. Oldest is most urgent: the seller is waiting.
SELECT c.id, c.tenant_id, c.provider, c.created_at,
       c.provider_consignment_id, c.tracking_code
  FROM consignments c
 WHERE c.status = 'BOOKING_UNKNOWN'
 ORDER BY c.created_at;

-- Is it one provider, one shop, or everything?
SELECT provider, COUNT(*), MIN(created_at)
  FROM consignments WHERE status = 'BOOKING_UNKNOWN'
 GROUP BY provider;
```

---

## Recovery

Section 36's order, and it matters:

1. **Ask the provider**, using the idempotency key or merchant reference we sent.
   Their answer is the only authority.
2. **It exists** → attach the consignment id and move the parcel to `BOOKED`.
   The money core picks up from there unchanged.
3. **It does not exist** → the parcel may be booked, once, deliberately, by a
   person who has just confirmed it does not exist.
4. **They cannot say** → it stays `BOOKING_UNKNOWN` and a support case is
   opened. An unresolved unknown is better than a wrong resolution.

Nothing here is automatic. That is the design.

---

## Seller impact

- The parcel shows as **booking unconfirmed** with the words "do not rebook —
  we are checking" (error code `BOOKING_AMBIGUOUS`, which already carries that
  Bangla copy).
- No stock has moved and no receivable exists: both happen on a confirmed
  dispatch, so an unknown leaves the money untouched.
- The seller can still print, pack and hand the parcel over. The record catches
  up.

---

## Verification

- The queue drains and stays near zero for an hour.
- **No duplicate consignments**: cross-check with
  [DUPLICATE_BOOKING.md](DUPLICATE_BOOKING.md) before declaring this closed.
- Every parcel resolved to `BOOKED` has a provider consignment id. One without
  is a guess, not a resolution.
