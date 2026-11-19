# Runbook: customer order confirmations (V4 WP2.1, policy WP2.2, call queue + SMS link WP2.3, automation hooks WP2.4)

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
  → the open confirmation becomes `SUPERSEDED` and a new one starts and is
  decided again (`superseded_by` links them). **Order cancelled** → the open confirmation is
  `CANCELLED` with no method.
- Starting one: `POST /v1/orders/{id}/confirmation` (`REQUEST` | `SELLER_CHAT`),
  or the optional `confirmation` field on `POST /v1/orders`,
  `POST /v1/chat-orders/{id}/confirm` and an offline `ORDER` create through
  `/v1/sync/mutations` (the quick-order sheet's two buttons). All idempotent per
  order. Order sources (website/store) cannot set it.

## Policy (WP2.2): who gets called

`app/confirmations/policy.py` `decide(facts, rules)` runs once on every new
`PENDING` row (`ConfirmationService.start`, and the restart after a material
edit) and moves it at once: `AUTO_CONFIRM` → `CONFIRMED` (`AUTO_TRUSTED`, the
order moves as `system:customer_confirmation`), `CALL` → `CALL_QUEUED`,
`SELLER_REVIEW` → `SELLER_REVIEW` (+ `confirmation.review_needed`). First match wins;
the order is fixed, the thresholds are the shop's `shop_delivery_rules`
(`PATCH /v1/delivery-rules`):

| # | When | Decision / `reason_code` |
|---|---|---|
| 1 | customer flag `BLOCKED` | `SELLER_REVIEW` / `CUSTOMER_BLOCKED` |
| 2 | external success < `risk_low_bps` (5000) over ≥ 5 parcels, or own-shop band `HIGH` | `SELLER_REVIEW` / `HIGH_RISK` |
| 3 | COD > `max_auto_cod_paisa` (৳5,000) | `CALL` / `HIGH_VALUE` |
| 4 | ≥ `trusted_repeat_min` (2) own deliveries and band `LOW` | `AUTO_CONFIRM` / `TRUSTED_REPEAT` |
| 5 | fresh external ≥ `trusted_network_bps` (8500) over ≥ 5 parcels, no own-shop return | `AUTO_CONFIRM` / `TRUSTED_NETWORK` |
| 6 | anything else | `CALL` / `VERIFY_NEW` |

`call_nobody` turns rules 3–6 into `SELLER_REVIEW` / `SHOP_MANUAL`;
`call_everyone` turns rules 4–5 into `CALL` / `CALL_EVERYONE`. Rules 1–2 always
stand. Facts come from stored data only (`app/confirmations/facts.py`): the
seller-private flag, the own-shop band (`app/customers/risk.py`, the customer
screen's band) and the newest answered lookup of an **enabled, shipped** external
provider. No such provider or answer → external is `null`: the external half of
rule 2 and rule 5 are skipped. An expired answer still counts for rule 2, never
for rule 5. Nothing here calls a provider. The row keeps `decision`,
`reason_code` and `facts` (counts, band, thresholds — no phone or name), returned
by `GET /v1/orders/{id}/confirmation` for the "why" display.

## Call queue and SMS link (WP2.3)

**Queue.** `GET /v1/confirmations/queue` (`order.write`) lists `SELLER_REVIEW` rows
and, while voice is off (until Phase 3), `CALL_QUEUED` rows: COD high to low, then
the oldest order. The phone's Call queue screen (and the orders-list banner) reads it.

**Actions.** `POST /v1/orders/{id}/confirmation/{action}`, only for an order in the
queue (else `409 CONFIRMATION_NOT_IN_QUEUE`):

- `call_now` — refused during the shop's quiet hours (`shop_delivery_rules`
  `quiet_start`/`quiet_end`, Asia/Dhaka, may wrap midnight) with
  `409 CONFIRMATION_QUIET_HOURS` unless `{"override_quiet": true}`. Opens a
  `MANUAL_CALL` attempt (dialing again before an answer reuses it) and returns the
  number for the `tel:` link. Each call writes `confirmation.call_started`
  (`quiet_override` in its context) and `privacy.phone_revealed`
  (`confirmation_call`). 30 calls per user per 10 minutes, then `429`.
- `mark_confirmed` / `mark_cancelled` — `SELLER_CONFIRM` / `SELLER_CANCEL`; method
  `MANUAL_CALL` when an open manual call exists, else `SELLER`. A repeated tap after
  the answer landed returns the same row.
- `mark_no_answer` ("Dhoreni") — closes the manual call `NO_ANSWER`; the row stays
  in review (the state machine has no move for it; no `confirmation.no_answer`).

A `CALL_QUEUED` row the seller acts on goes `CALL_QUEUED → SELLER_REVIEW`
(`VOICE_UNAVAILABLE`) first, with **no** seller alert (the seller is the one acting).

**SMS link.** The `follow_up_confirmations` job (worker, minutes 4, 9, … 59) takes
due rows in their own tenant session:

- `NO_ANSWER` with calls left and the 30-minute wait over → `CALL_QUEUED` (`RETRY`).
- `NO_ANSWER` after the last call → if `confirmation_sms_link_enabled` is on, an SMS
  transport is configured (`SMS_TRANSPORT`), `PUBLIC_WEB_URL` is set and the order has
  a customer: outside quiet hours a link row (token = 22 url-safe chars derived from
  the link id; only its SHA-256 stored; 6 h) + `SMS_LINK` attempt, `→ SMS_LINK_SENT`,
  and `confirmation.link_requested`; during quiet hours it waits. Otherwise
  `→ SELLER_REVIEW` (`ATTEMPTS_EXHAUSTED`).
- `SMS_LINK_SENT` past 6 h → open links expired, `→ SELLER_REVIEW` (`LINK_EXPIRED`).

The `confirmation.link_requested` handler sends one SMS:
`<shop, ≤ 24 chars>: order Tk 1,250. Confirm/cancel: <PUBLIC_WEB_URL>/c/<token>`
("Tk", so the text stays one GSM-7 segment). The attempt records provider, reference
and segments. A refused send (or no customer) kills the link and moves the row to
the seller; a temporary failure raises, so the outbox retries with the same link.

**Public page.** `apps/web/app/c/[token]` calls `GET /v1/public/confirmations/{token}`
(reads only — SMS apps prefetch links) and `POST` with `{"result": "confirm" | "cancel"}`
once (`LINK_CONFIRM` / `LINK_CANCEL`, the order moves as the customer). Every reply
has one shape; unknown, expired and closed links read `UNAVAILABLE`, a used one
`ANSWERED` with its result. 60 views and 10 answers per minute per client address.

## Automation hooks (WP2.4)

Workflow triggers (V3.4 engine, consumed when the outbox row is written, in the
same transaction): `confirmation.confirmed` and `confirmation.cancelled` start
**only when `order_outcome = APPLIED`** (the answer moved the order; an answer the
order could not take goes to the seller alert, and a confirmation ended by the
order's own cancel is `order.cancelled`); `confirmation.no_answer` and
`confirmation.callback_requested` (IVR, Phase 3); `intake.draft_ready` (subject: the
draft — only draft-level actions and the shop-level ones fit it).

Actions:
- `REQUEST_CONFIRMATION` (order triggers) = `ConfirmationService.start(order,
  requested_by=None)`; the policy decides (step outcome `CALL` / `AUTO_CONFIRM` /
  `SELLER_REVIEW`). A repeat → `ALREADY_REQUESTED`; a non-draft order → SKIPPED
  `ORDER_NOT_DRAFT`; flag off → SKIPPED `CONFIRMATION_ENGINE_DISABLED`. Switching a
  workflow with this step on while the flag is off is refused (blocker
  `CONFIRMATION_ENGINE_DISABLED`; a recipe install stays a draft).
- `CONVERT_DRAFT_TO_ORDER` (draft trigger) → always SKIPPED `AUTOPILOT_DISABLED`
  until Phase 4 ships the Autopilot gates. No draft becomes an order from a workflow.

Recipes: `order_confirm_customer` ("Order → confirm with the customer",
`order.created` → `REQUEST_CONFIRMATION`) and `confirmation_book_track` ("Customer
confirmed → book courier → send tracking", `confirmation.confirmed` →
`BOOK_COURIER` + `SEND_TEMPLATE`). The tracking update goes on the chosen
transactional channel (email / WhatsApp); there is no SMS channel in
`SEND_TEMPLATE`. A shop that also keeps the V3.4 `order.confirmed → book` workflow
books once: the second booking step records `ALREADY_BOOKED`.

**Detection**: Automation → run history; skipped steps show the reason above.
**Containment**: switch the workflow off (runs in progress stop with
`WORKFLOW_DISABLED`) or the flag off (steps skip). **Verification**: with the flag
on for a test shop, install `order_confirm_customer`, create an order → one run,
step `CALL`, the order in the call queue; Confirm it → a `confirmation.confirmed`
run books once.

## Flag

| Switch | Where | Default |
|---|---|---|
| `confirmation_engine_enabled` | `feature_flags` row (global or per shop), admin console | **off** |
| `confirmation_sms_link_enabled` | same; needs `SMS_TRANSPORT` + `PUBLIC_WEB_URL` too | **off** |

Off: `POST .../confirmation` answers `FEATURE_DISABLED`, the `confirmation` field
on order creates is ignored (the order still lands — a replayed offline create
must never fail), the quick-order sheet shows the single **Create order**, and
workflow `REQUEST_CONFIRMATION` steps skip (`CONFIRMATION_ENGINE_DISABLED`).
`GET .../confirmation` always answers and reports `enabled`. Turning the flag off
does not touch open rows: order edits and cancels still end them correctly.

## Outbox topics (handlers in `app/confirmations/jobs.py`)

`confirmation.confirmed`, `confirmation.cancelled`, `confirmation.no_answer`,
`confirmation.callback_requested`, `confirmation.review_needed`,
`confirmation.link_requested` (WP2.3: sends the SMS). Payloads carry ids
and codes only (no phone, name or text). `review_needed`, and a confirmed /
cancelled event whose `order_outcome` is not `APPLIED`, raise one
`CONFIRMATION_NEEDS_ATTENTION` notification per confirmation and reason (dedupe
key `confirmation:<id>:<reason>`). The worker imports the module at start; a topic
without a handler would sit at the head of the outbox.

## Detection

- Outbox rows `topic LIKE 'confirmation.%'` stuck in `PENDING`/`FAILED`: handler
  error (logs `app.confirmations`, `confirmation event`).
- Decision mix: `SELECT reason_code, count(*) FROM order_confirmations GROUP BY 1`.
  Almost everything `SHOP_MANUAL` / `CALL_EVERYONE` = the shop's switches; a jump
  in `HIGH_RISK` after a provider change = check that provider's answers.
- Many `order_outcome IN ('ORDER_CHANGED','ORDER_NOT_DRAFT')`: sellers edit or
  confirm orders while a confirmation runs — expected occasionally; a spike means
  a client is editing orders automatically.
- `IntegrityError` on `uq_order_confirmations_open_order`: two starts raced; the
  request is idempotent, the client retry returns the existing row.
- Sweep: log `confirmation follow-up finished` (counts `RETRY`, `LINK`, `QUIET`,
  `REVIEW`, `EXPIRED`, `failed`); `failed > 0` → `confirmation follow-up failed` with
  the confirmation id and error class.
- Links: log `confirmation link` (`outcome`, `segments`);
  `SELECT status, count(*) FROM confirmation_attempts WHERE channel = 'SMS_LINK' GROUP BY 1`
  — `NOT_CONFIGURED` / `REJECTED` = gateway problem; `segments > 1` = a long shop name
  in Bangla (UCS-2).
- Call reveals: `audit_logs` `privacy.phone_revealed` with reason `confirmation_call`
  far above the number of queue rows = someone reading numbers out; the per-user
  limit returns `429`.

## Containment

Turn `confirmation_engine_enabled` off for the shop (or globally). To stop
auto-confirmation only, the seller turns on `call_everyone` (or `call_nobody`).
To stop SMS links only, turn `confirmation_sms_link_enabled` off: due rows then go
to the seller; links already sent keep working until they expire.
Nothing books a courier from a confirmation in WP2.1–2.2 (that is WP2.4 through the automation
engine), so the blast radius is order status only.

## Recovery

- An order a customer confirmed but that stayed `DRAFT` (`order_outcome` not
  `APPLIED`): the seller confirms it from the order screen as usual.
- Never edit `order_confirmations.state` by hand; a stuck open row ends when its
  order is cancelled or materially edited.
- A link SMS that never arrived (outbox event dead-lettered): the row stays
  `SMS_LINK_SENT` and goes to the seller when the link expires (6 h), or the seller
  answers from the queue sooner once it is in review.
- A leaked link: expire it (`UPDATE confirmation_links SET expires_at = now()` for that
  confirmation); the next sweep hands the row to the seller.

## Verification

`cd backend && python -m pytest tests/test_confirmations.py tests/test_confirmation_policy.py tests/test_confirmation_queue.py tests/test_confirmation_links.py -q`
(state table, illegal moves, order guards, supersede/cancel hooks, handlers, API,
sheet paths; every policy rule, boundary, override and external case; queue order,
quiet hours, call reveal + audit, outcomes, rate limits; link text, token, sweep,
send, expiry, public page);
Flutter `flutter test test/features/intake_screens_test.dart test/features/call_queue_screen_test.dart`;
web `cd apps/web && npm test` (`tests/confirm-link.test.mjs`).
Live SMS needs a gateway (`SMS_PROVIDER_CONTRACT_REQUIRED`): until then either the
transport is off (no link is made; the row goes to the seller) or
`SmsGatewayTransport` answers `NOT_CONFIGURED` (the link is killed and the row goes
to the seller).
