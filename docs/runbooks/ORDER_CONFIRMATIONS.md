# Runbook: customer order confirmations (V4 WP2.1)

A confirmation records whether the **customer** really wants an order, separate
from the order's own status (rule L7). It lives in `order_confirmations`
(migration `a42001`), with one row per call or link in `confirmation_attempts`
and hashed SMS-link tokens in `confirmation_links` (WP2.3).

- One **open** confirmation per order (partial unique index
  `uq_order_confirmations_open_order`). Open = `PENDING`, `CALL_QUEUED`, `CALLING`,
  `NO_ANSWER`, `SMS_LINK_SENT`, `SELLER_REVIEW`; terminal = `CONFIRMED`,
  `CANCELLED`, `SUPERSEDED`.
- Every move is a row of `app/confirmations/state_machine.py` `TRANSITIONS`
  (`(from, to)` → allowed causes). Anything else is refused with
  `CONFIRMATION_TRANSITION_ILLEGAL`.
- **Customer yes** → the order moves `DRAFT → CONFIRMED`, audit actor
  `system:customer_confirmation`, **only** while the order is still `DRAFT` at the
  `order_version` the confirmation snapshotted. Otherwise the answer is kept
  (`order_outcome` = `ORDER_CHANGED` / `ORDER_NOT_DRAFT`) and the seller gets a
  `CONFIRMATION_NEEDS_ATTENTION` alert. **Customer no** → order `CANCELLED`,
  reason `CUSTOMER_DECLINED`, same guard; the customer is never flagged.
- **Material edit** (items changed, or COD more than 10% away from the snapshot)
  → the open confirmation becomes `SUPERSEDED` and a new `PENDING` one starts
  (`superseded_by` links them). **Order cancelled** → the open confirmation is
  `CANCELLED` with no method.
- Starting one: `POST /v1/orders/{id}/confirmation` (`REQUEST` | `SELLER_CHAT`),
  or the optional `confirmation` field on `POST /v1/orders`,
  `POST /v1/chat-orders/{id}/confirm` and an offline `ORDER` create through
  `/v1/sync/mutations` (the quick-order sheet's two buttons). All idempotent per
  order. Order sources (website/store) cannot set it.

## Flag

| Switch | Where | Default |
|---|---|---|
| `confirmation_engine_enabled` | `feature_flags` row (global or per shop), admin console | **off** |

Off: `POST .../confirmation` answers `FEATURE_DISABLED`, the `confirmation` field
on order creates is ignored (the order still lands — a replayed offline create
must never fail), and the quick-order sheet shows the single **Create order**.
`GET .../confirmation` always answers and reports `enabled`. Turning the flag off
does not touch open rows: order edits and cancels still end them correctly.

## Outbox topics (handlers in `app/confirmations/jobs.py`)

`confirmation.confirmed`, `confirmation.cancelled`, `confirmation.no_answer`,
`confirmation.callback_requested`, `confirmation.review_needed`. Payloads carry ids
and codes only (no phone, name or text). `review_needed`, and a confirmed /
cancelled event whose `order_outcome` is not `APPLIED`, raise one
`CONFIRMATION_NEEDS_ATTENTION` notification per confirmation and reason (dedupe
key `confirmation:<id>:<reason>`). The worker imports the module at start; a topic
without a handler would sit at the head of the outbox.

## Detection

- Outbox rows `topic LIKE 'confirmation.%'` stuck in `PENDING`/`FAILED`: handler
  error (logs `app.confirmations`, `confirmation event`).
- Many `order_outcome IN ('ORDER_CHANGED','ORDER_NOT_DRAFT')`: sellers edit or
  confirm orders while a confirmation runs — expected occasionally; a spike means
  a client is editing orders automatically.
- `IntegrityError` on `uq_order_confirmations_open_order`: two starts raced; the
  request is idempotent, the client retry returns the existing row.

## Containment

Turn `confirmation_engine_enabled` off for the shop (or globally). Nothing books a
courier from a confirmation in WP2.1 (that is WP2.4 through the automation
engine), so the blast radius is order status only.

## Recovery

- An order a customer confirmed but that stayed `DRAFT` (`order_outcome` not
  `APPLIED`): the seller confirms it from the order screen as usual.
- Never edit `order_confirmations.state` by hand; a stuck open row ends when its
  order is cancelled or materially edited.

## Verification

`cd backend && python -m pytest tests/test_confirmations.py -q` (state table,
illegal moves, order guards, supersede/cancel hooks, handlers, API, sheet paths);
Flutter `flutter test test/features/intake_screens_test.dart`.
