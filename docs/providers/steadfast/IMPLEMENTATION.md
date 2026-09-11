# Steadfast V1 — implementation notes

**Status: Phase C code complete; external verification pending.**

Everything that can responsibly be built without a merchant account is built,
tested and shipped. What remains is a category of work no amount of engineering
substitutes for: calling the live provider with real credentials and recording
what it actually does.

| | |
|---|---|
| **Source document** | Steadfast Courier Limited — API Documentation **V1**, operator-supplied Google Docs export |
| **Sanitized copy** | [`API_Documentation_V1.sanitized.html`](API_Documentation_V1.sanitized.html) |
| **Normalized reading** | [`CONTRACT.md`](CONTRACT.md) |
| **Contract as code** | `backend/app/couriers/steadfast/contract.py` |
| **Manifest** | `docs/provider_notes/steadfast.yaml` |
| **Date read and transcribed** | 2026-09-11 |
| **Base URL** | `https://portal.packzy.com/api/v1` |
| **Auth model** | Static merchant headers `Api-Key`, `Secret-Key`, `Content-Type: application/json` on every request |
| **Live credential test** | **Not performed.** `STEADFAST_LIVE_CREDENTIAL_TEST_REQUIRED` |

The two example credential literals in the document's PHP snippet were redacted
before the copy was committed (`<REDACTED_EXAMPLE_API_KEY>`,
`<REDACTED_EXAMPLE_SECRET_KEY>`) and were never used to call the provider. The
raw as-delivered export is gitignored.

---

## What is implemented

| Capability | Endpoint | ecomsbd code | Live-credential verification |
|---|---|---|---|
| Credential validation | `GET /get_balance` | `couriers/accounts.py` | not needed to prove the shape; needed to prove a real key works |
| Create single | `POST /create_order` | `couriers/booking.py` | **required** |
| Create bulk | `POST /create_order/bulk-order` | `couriers/booking.py` | **required** |
| Status by consignment id | `GET /status_by_cid/{id}` | `couriers/status_sync.py` | recommended |
| Status by invoice | `GET /status_by_invoice/{invoice}` | `couriers/recovery.py`, `status_sync.py` | recommended |
| Status by tracking code | `GET /status_by_trackingcode/{code}` | `couriers/status_sync.py` | recommended |
| Balance | `GET /get_balance` | `couriers/accounts.py` | recommended |
| Create return request | `POST /create_return_request` | `couriers/returns.py` | **required** |
| Return request lookup | `GET /get_return_request/{id}` | `couriers/returns.py` | **required** (schema unverified) |
| Return request list | `GET /get_return_requests` | `steadfast/client.py` | **required** (schema unverified) |
| Payments list | `GET /payments` | `couriers/payments.py` | **required** (schema unverified) |
| Payment with consignments | `GET /payments/{payment_id}` | `couriers/payments.py` | **required** (schema unverified) |
| Police stations | `GET /police_stations` | `steadfast/client.py` | optional (schema unverified) |

Capabilities the document positively lacks — price quote, customer stats,
cancel, pickup-store list — report `Unavailable` with a reason the UI shows.
They are `false` in the manifest, not `unknown`: we know Steadfast V1 has no
such endpoint.

---

## Exact status map

Eleven documented statuses, exact-match only. `delivered_approval_pending`
contains `delivered`; a prefix or substring match would settle a COD receivable
on a parcel the courier has not approved, let alone paid for. There is a test
for that specific confusion.

| Steadfast status | ecomsbd state | Final? | Moves money? | Notes |
|---|---|---|---|---|
| `in_review` | `BOOKED` | no | no | |
| `pending` | `BOOKED` | no | no | |
| `hold` | `BOOKED` | no | no | says nothing about the outcome; keeps polling |
| `delivered_approval_pending` | `OUT_FOR_DELIVERY` | no | **no** | awaiting the courier's admin approval |
| `partial_delivered_approval_pending` | `OUT_FOR_DELIVERY` | no | **no** | also needs quantities |
| `cancelled_approval_pending` | `RETURNING` | no | **no** | |
| `unknown_approval_pending` | `BOOKED` | no | **no** | document says contact support |
| `delivered` | `DELIVERED` | yes | yes | stops polling |
| `partial_delivered` | `PARTIAL_DELIVERED` | yes | **only after a person supplies quantities** | stops polling |
| `cancelled` | `CANCELLED` | yes | yes | stops polling |
| `unknown` | `BOOKED` | no | no | document says contact support |
| *anything else* | — | no | no | stored raw, signalled, changes nothing |

### Why approval-pending is provisional

The document's own descriptions draw the line. The four `*_approval_pending`
entries say "waiting for admin approval"; `delivered`, `partial_delivered` and
`cancelled` say "balance added" / "balance updated". So the pending states move
the parcel's *visible* status — the seller sees progress — and no COD becomes
collectible, no ledger entry is written and no profit snapshot is frozen.

### Why `partial_delivered` does not write its own status

`PARTIAL_DELIVERED` is terminal in ecomsbd and carries money: reaching it means
a realized-revenue figure and a stock restoration computed from per-item
quantities. The Steadfast status string carries **no quantities anywhere in the
document**. So the observation sets `needs_quantity_resolution`, leaves the
parcel in flight, and a person supplies the numbers through the existing
partial-delivery workflow. Nothing is inferred — not half, not one, not
all-but-one.

### There are no transit states

Steadfast's entire pre-delivery vocabulary is `in_review` and `pending`. It
documents no `picked_up`, no `in_transit` and no `out_for_delivery`. ecomsbd's
richer lifecycle keeps those for manual mode; a Steadfast parcel never enters
them except to represent the documented approval-pending states. Claiming a
parcel is "in transit" because it is not yet delivered would be ecomsbd
inventing a fact.

---

## Booking safety model

    prepare -> COMMIT -> HTTP -> apply outcome -> COMMIT

The middle commit is load-bearing. If the process dies during the provider call,
the booking attempt — carrying the exact invoice that was sent — has already
survived. Without it a parcel could exist at the courier with nothing on our
side that knows its reference, which is a parcel nobody can ever reconcile. It
also means no database transaction is held open across a network call.

### Three outcomes

| Outcome | ecomsbd state | Stock | Receivable | Retry |
|---|---|---|---|---|
| Provider confirmed with an id | `BOOKED` | decremented | opened | n/a |
| Provider answered and refused | `NOT_BOOKED` | untouched | none | safe, same invoice |
| Anything else | `BOOKING_UNKNOWN` | untouched | none | **never automatic** |

"Anything else" includes a read timeout, a connection reset, a 5xx after the
request was sent, an HTML error page, malformed JSON, a body we cannot read,
and an unexpected exception from the adapter. The default is the unsafe answer;
only a *proof* that nothing was created — a connect failure, or a deterministic
4xx rejection — produces a clean failure.

### The merchant reference

`CP-20260911-0042` for the first parcel of an order; `CP-20260911-0042-2` for a
replacement after the first was cancelled. Three properties, all required:

* **stable across retries** — every attempt at the same parcel sends the same
  string, which is what makes the provider's uniqueness constraint useful to us;
* **never reused** — a courier statement naming `…-0042` is unambiguously about
  the first parcel, forever;
* **inside the documented character set** — letters, digits, hyphens and
  underscores, validated before anything is persisted as sent.

### `BOOKING_UNKNOWN` recovery

Rests on one asymmetry the documentation forces:

> **Existence can be proven. Absence cannot.**

`GET /status_by_invoice/{invoice}` returning a `delivery_status` proves the
parcel exists: the booking is recovered, and the deferred stock movement and
receivable are applied. Anything else — including a 404, because the document
describes **no** "not found" body — proves nothing, so the attempt stays
`BOOKING_UNKNOWN` and is asked again on exponential backoff (60s doubling to a
1h cap, 8 attempts by default). After that it goes to a person with the evidence
attached.

Declaring that a parcel was never created is the **only** path that makes an
order bookable again, it requires a reason, it is audited with who said so, and
only a human can take it.

**What recovery deliberately does not claim:** the provider's consignment id.
`status_by_invoice` returns a status and nothing else, and Steadfast documents
no way to obtain an id from an invoice. A recovered parcel is therefore handled
by its merchant reference — which is sufficient, because the same by-invoice
lookup is what keeps polling it. This is why all three documented status lookup
forms are implemented rather than just the one.

### Bulk

Chunked at **50**, not the documented 500. A failed 500-item request leaves 500
parcels in an unknown state; permission to send that many is not a reason to. A
chunk whose answer is lost is **never resent** — each of its items becomes
independently ambiguous and is recovered by its own invoice.

Results are matched back **by invoice, never by position**. The document
guarantees no ordering, and matching by position when the provider reorders or
drops an entry attributes one order's consignment id to a different order.

### `cod_amount` is whole taka

The document types it `numeric` with the example `1060` — taka, not paisa. A
courier collects banknotes and poisha coins are out of circulation, so the
boundary rounds half-up to whole taka and **returns the paisa residual**, which
is recorded on the booking attempt. Silent rounding is how an unexplainable
few-paisa reconciliation difference appears months later.

---

## Status synchronisation

Polling, adaptively. Steadfast documents no webhook, so this is not a fallback —
it is the production path, and it is built to be sufficient on its own.

| Parcel age | Interval |
|---|---|
| < 24h since booking | 20 minutes |
| < 7 days | 1 hour |
| older | 6 hours |
| settled | off the schedule |

A failure streak backs off on top of that. All intervals are configurable
(`COURIER_POLL_*`). A parcel is looked up by provider consignment id where we
have one, then tracking code, then invoice.

Every answer is recorded as an immutable observation first. Identical repeat
answers bump `observation_count` rather than inserting a row, so a parcel polled
hourly for a week is one event and a count of 168.

---

## Returns

`POST /create_return_request` takes exactly one of `consignment_id`, `invoice`
or `tracking_code` — the document says "or" and does not say which wins if
several are sent, so sending several would leave the choice to the provider.

Steadfast documents **no idempotency** for return creation, so ecomsbd provides
it: a deterministic key unique per shop, a row lock, and an active-request check
in which an `UNKNOWN` request **blocks** a second one. A lost answer does not
mean the courier did not receive it, and a parcel collected twice is a charge
the seller cannot undo.

A completed return request never restores stock from this module. That is a
delivery outcome and goes through the existing return domain, so the stock
movement, the receivable and the profit snapshot happen together.

---

## Payment sync model

This is where the documentation runs out: `/payments` and `/payments/{id}` give
a path and a method and **no schema at all**. The integration is complete
anyway.

    list -> dedupe by provider payment id -> fetch detail -> store raw
         -> normalize -> payout + lines + adjustments -> reconciliation engine

Four properties, each tested:

1. **One payment, one payout, forever.** Unique key on
   `(tenant, provider, provider_payment_id)`. A re-run updates `last_seen_at`;
   it does not create a second payout or a second set of ledger entries.
2. **A changed payment is flagged, never rewritten.** If the provider's copy
   hashes differently after import, the row moves to `CHANGED` and a person
   decides. Silently rewriting a settled ledger entry would make a seller's
   reconciled month stop reconciling with no event explaining why.
3. **Matching uses identity, not amounts.** Consignment id, tracking code or
   our invoice. Amount-only never auto-matches — that rule lives in the existing
   reconciliation engine, which this feeds rather than bypasses.
4. **An unexplained deduction stays unexplained.** Itemised charges become their
   own adjustments; a payload giving only a net figure produces exactly one
   `UNKNOWN_DEDUCTION` carrying the provider's own words.

Pagination is followed **only when the response declares it**. No `?page=`
parameter is invented against an endpoint that documents none: one that ignores
it would return page one forever, making the loop either infinite or silently
truncating.

Every payout produced this way carries `schema_undocumented: true` and the
observed field names in its metadata, and the seller-facing Money screen says so
too.

### Courier charge truth

Master spec section 85's hierarchy, and where Steadfast sits in it:

    provider-settled actual  >  provider payment detail  >  booking response
        >  merchant rate  >  manual  >  estimate  >  missing

A Steadfast parcel gets a `SETTLED` charge **only** from payment data that names
the charge. **No charge is recorded at booking.** `cod_amount` is documented as
the amount to collect "including all charges" with no decomposition, so calling
any part of it a delivery fee would be an invention. Profit quality therefore
moves `ESTIMATED -> ACTUAL` when a payment arrives that itemises, and stays
`ESTIMATED` when one arrives that does not.

---

## Webhooks — `STEADFAST_WEBHOOK_CONTRACT_REQUIRED`

The supplied document has **no webhook section at all**. No endpoint, no
signature header, no algorithm, no payload, no event id, no retry contract.

Built and tested:

* `POST /v1/webhooks/couriers/steadfast`
* raw-body persistence before anything is decided
* replay protection by body hash, with a delivery counter
* the delivery state machine, including `NOT_CONFIGURED` as a distinct state
* verifier and parser ports, and a constant-time signature comparator
* queue hand-off, and a status-application path shared with polling

Deliberately absent: the signature header name, the HMAC algorithm, the payload
field names, the provider's event id.

`SteadfastWebhookVerifier.is_configured` returns `False` unconditionally. A
verifier that returned `True` because there is nothing to check would not be a
disabled webhook — it would be an open endpoint letting anyone mark any parcel
delivered and move a seller's money. The route answers **202 NOT_CONFIGURED**,
stores the body (header *names* only; a signature header is credential-adjacent)
and processes nothing. It is excluded from the public OpenAPI schema.

`Settings` additionally refuses to boot a deployed environment with
`STEADFAST_WEBHOOK_ENABLED=true`, because no flag supplies a contract.

Polling is the complete V1 synchronisation path and depends on none of this.

---

## Verified facts, and explicit unknowns

### Verified from the document

* The eleven delivery statuses, and which three settle a balance.
* The five return-request statuses.
* Every create-order field, its optionality and its stated length limit.
* The bulk maximum (500) and the two response shapes (bare array, `data`-wrapped).
* The three status lookup forms.
* `cod_amount` is in BDT and includes all charges, with no decomposition given.
* Body-level `status` accompanies the HTTP status on every documented response.

### `UNVERIFIED` — documented endpoint, undocumented response schema

The typed fields on these are *inferred*: the parser looks for a small set of
plausible key names, records which one the provider actually used, and preserves
every unknown key verbatim. One live call replaces every inference below with a
fact — run the smoke tool with `--probe-undocumented`.

* `GET /payments` — every field.
* `GET /payments/{payment_id}` — every field, including the consignment list key
  and each consignment's amount and charge fields.
* `GET /get_return_request/{id}`, `GET /get_return_requests` — every field.
* `GET /police_stations` — every field.

### `UNKNOWN` — the document is silent

| | |
|---|---|
| `PROVIDER_CREATE_IDEMPOTENCY` | **UNKNOWN.** The document says `invoice` must be unique and never says what happens when one is reused. ecomsbd therefore claims no provider idempotency and provides its own: a stable invoice persisted before the call, a row lock, `BOOKING_UNKNOWN`, and recovery by invoice. |
| `WEBHOOK_CONTRACT` | **UNKNOWN.** No webhook section exists. |
| `RATE_LIMIT_CONTRACT` | **UNKNOWN.** No limit, no `429` semantics, no `Retry-After`. A literal 429 is the only thing treated as throttling. |
| `PAGINATION_CONTRACT` | **UNKNOWN.** No parameter documented for any list endpoint. |
| `ERROR_BODY_CONTRACT` | **UNKNOWN.** No error body is described anywhere, so classification is from the status line alone. |
| `DUPLICATE_INVOICE_BEHAVIOUR` | **UNKNOWN.** Until real testing shows otherwise, a duplicate-invoice response is treated as ambiguous. |
| `AUTH_LIFETIME` | **UNKNOWN.** No token, refresh, expiry or rotation is described; none is implemented. |
| Status-change timestamps | **UNKNOWN.** The status response carries none, so the UI says "checked", never "happened at". |
| Per-parcel charge breakdown at booking | **UNKNOWN.** None is given, so none is recorded. |
| Sandbox base URL | **UNKNOWN.** The document names none. |

---

## Live verification — what is still pending

`STEADFAST_LIVE_CREDENTIAL_TEST_REQUIRED`

No merchant API key exists in this environment, so nothing below has been
exercised against the real provider. Everything is code complete and tested
against contract fixtures over a fake transport.

Run, in this order, with a real account:

```bash
# 1. Read-only. Proves the credentials work and the balance parses.
python -m app.provider_smoke steadfast --account-id <uuid>

# 2. Read-only. Records the real field names of every undocumented endpoint.
python -m app.provider_smoke steadfast --account-id <uuid> --probe-undocumented

# 3. Read-only. Confirms a known parcel's status parses.
python -m app.provider_smoke steadfast --account-id <uuid> --invoice CP-...

# 4. Creates ONE REAL PARCEL. Needs a recipient fixture and a typed
#    confirmation. Do not run this without meaning to.
python -m app.provider_smoke steadfast --account-id <uuid> --allow-create \
    --recipient-name "..." --recipient-phone 01XXXXXXXXX \
    --recipient-address "..." --cod-amount 0
```

The tool never takes a credential on the command line, refuses to run with
`CI=true` unless `--allow-ci` is passed, and every default check is a read.

### Results to record here once run

| Check | Date | Result |
|---|---|---|
| Authentication + balance | — | not yet run |
| Status by invoice | — | not yet run |
| Status by consignment id | — | not yet run |
| `GET /payments` field names | — | not yet run |
| `GET /payments/{id}` field names | — | not yet run |
| `GET /get_return_requests` field names | — | not yet run |
| Single create (real parcel) | — | not yet run |
| Duplicate-invoice behaviour | — | not yet run |
| Bulk create (real parcels) | — | not yet run |
| Return request (real) | — | not yet run |

When a check runs, update this table **and** the corresponding entry in
`docs/provider_notes/steadfast.yaml`. A capability only moves from `unknown` to
`true` with a date and evidence.

---

## Manual courier mode

Unchanged and unconditional. A seller with no Steadfast account, a disconnected
one, one needing reconnection, or a courier that is down, can still record a
parcel and its tracking code by hand, upload the payout statement, and run the
same reconciliation engine over the imported lines. Steadfast enhances ecomsbd;
it is not a single point of failure, and the UI presents manual mode as a real
path rather than an apology.
