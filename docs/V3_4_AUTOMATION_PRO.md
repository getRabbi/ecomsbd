# V3.4 Automation Pro

The V2 automation rules, extended into multi-step workflows. There is still **one
engine**: `automation_rules` is the workflow, `automation_executions` the run, and
the existing `dispatch_automation` ARQ cron (every 30 s) advances every run. A V2 rule
is a published one-step workflow; the `/v1/automation/rules` endpoints still work.

## Model

| Table | What it holds |
|---|---|
| `automation_rules` | The workflow: name, editable `draft`, `published_version`, on/off, `recipe_key`. |
| `automation_workflow_versions` | Immutable published definitions plus the compiled `plan`. |
| `automation_executions` | One run per `(workflow, trigger event)`, which is unique. Adds `version_id`, `cursor` (current step), `retryable`, causation (`depth`, `ancestry`, `parent_execution_id`), `subject_key`, `wait_key`, `source` (EVENT/TEST) and start/finish times. |
| `automation_step_runs` | The receipt for one step of one run, unique `(run, step)`. It is written in the same transaction as the step's side effect. |
| `automation_attempts` | History log; now records the step. |

Statuses: `QUEUED`, `RUNNING` (only while a courier booking call is in flight),
`WAITING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `SKIPPED` (not started: conditions,
loop guard). Migration `a34001` maps V2 `PENDING`/`RETRY` to `QUEUED` and `DONE` to
`SUCCEEDED`, and publishes every V2 rule as version 1.

## Definitions

`WHEN trigger → IF conditions → THEN steps`, as JSON drawn from closed lists
(`app/automation/schemas.py`). There is no expression language, URL field or code.

- **Steps**: `action`, `delay` (`duration` minutes / `until_time` HH:MM in the shop's
  timezone, optionally not before tomorrow / `followup_due` of an earlier follow-up
  step), `wait_event` (with a required timeout and `continue`/`stop` on timeout), and
  `branch` (IF/ELSE, each arm a nested list that rejoins the next step).
- **Conditions**: `all`/`any` plus one level of nested groups. Fields are read from
  the system of record: order status, source, COD, payment method, courier, parcel
  status, SKU, items in stock, originating store, customer tag, segment, mark,
  returned parcels, transactional and marketing consent, stock on hand, store
  platform, sync code, entered segment and alert kind. The event's own facts (for
  example the status the order moved *to*) outrank live facts in entry conditions.
  Branches read live facts.
- **Limits**: 30 steps, 15 actions, 5 waits, nesting depth 4, delays up to 30 days,
  one courier booking and one task step per workflow, 50 workflows per shop.
- Publishing compiles the tree into a flat plan that only links forward, so it is
  acyclic by construction. It rejects actions, conditions and waits the trigger's
  subject cannot supply (for example a message on `inventory.low`).

## Triggers (existing domain events, consumed when written)

`app/automation/triggers.py` interprets outbox events in the producer's own
transaction. No polling for emitted events.

| Trigger | Source |
|---|---|
| order created / website-store order / confirmed / cancelled / status changed | `order.created`, `order.status_changed` |
| courier booked / status changed / delivered / returned (RTO) | `order.booked`, `consignment.status_changed` |
| COD received | **new** `cod.settled`, when a receivable becomes SETTLED |
| payout overdue / reconciliation issue / follow-ups due | **new** `alert.raised`, when a V2.2 smart alert is newly raised |
| stock low | `inventory.updated` crossing the item's own low-stock level (payload gains `quantity_delta`) |
| store sync failed | **new** `integration.issue_opened`, once per problem |
| customer entered segment | REPEAT on the order that crosses it; INACTIVE from `scan_segment_entries` (daily, the only time-based scan) via **new** `customer.segment_entered` |
| customer replied | **new** `customer.replied`, from a non-STOP WhatsApp text of a known customer (the text is never stored) |
| follow-up completed | **new** `followup.completed` |

Every new topic has a no-op outbox handler, so none of them blocks the outbox.

## Execution and idempotency

- One step per transaction, committed with its receipt. A retry resumes at the
  cursor, and a step with a SUCCEEDED/SKIPPED receipt is never run again.
- A failed step's local writes roll back (savepoint). Transient errors retry with
  backoff up to 5 times, then the run is `FAILED` with `retryable=true`. A
  conflict (a state the seller can change) is retryable; a validation or not-found
  error needs a fix.
- Delays and event waits are rows: `WAITING` with `next_attempt_at` as the wake or
  timeout time. A delay moves the cursor past itself before waiting, so waking
  cannot wait twice. The event that satisfies a wait marks the run due in its own
  transaction. A wait whose event has already happened continues at once.
- Replaying an event is safe: `(workflow, event)` is unique.
- Every side effect has a step-scoped key: messages use idempotency key
  `automation:{run}:{step}`, notifications a dedupe key, store pushes the automatic
  sync's own operation key, and webhooks a stable event id.
- **Courier booking** (`_book`): if the order already has a live parcel, the step
  succeeds as `ALREADY_BOOKED`. If a parcel is BOOKING/BOOKING_UNKNOWN, the run
  fails `BOOKING_UNKNOWN`, which is not retryable; booking recovery settles it. Only
  with no parcel does it mark the step IN_FLIGHT, commit, and call the V2 booking
  service, whose own BOOKING_UNKNOWN rules apply. A run left RUNNING by a crashed
  worker is requeued after 10 minutes, and the same parcel check decides again.
- A run finishes on the version it started with. Disabling a workflow cancels its
  queued and waiting runs.

## Loop protection

Each run records its causation. Events caused by a step start child runs one level
deeper. A workflow already in its own ancestry does not start again
(`LOOP_DETECTED`), chains stop past depth 3 (`DEPTH_LIMIT`), and one workflow runs
at most 5 times a day for the same subject (`REPEAT_LIMIT`). A stopped run is
recorded as `SKIPPED` with that reason, so the seller can see it.

## Actions (all existing services)

Send an order-update message (V3.3 transactional queue: consent, opt-out, channel
and template are re-checked, and there is a per-customer limit of 4 automated
messages per day; a blocked message is `SKIPPED` and the workflow continues), add or
remove a CRM tag, create and assign a follow-up, notify operations, finance or
inventory staff, create a task, book a courier, push the current status or tracking
to the connected store, retry a failed store sync (at most 3 attempts per problem),
label the order (`metadata_json.automation_labels`), and send the `automation.workflow`
webhook topic.

There is no ledger, payout, reconciliation, stock, order-status, HTTP or credential
action. Marketing goes through Campaigns: the "inactive customer" recipe tags a
campaign candidate, and the campaign then checks marketing consent.

## Recipes (English + Bangla, editable after install)

Website order → confirm by phone · confirmed → book courier → send update ·
returned → tag + follow-up · delivered → update website · low stock → notify ·
COD overdue → finance · store sync failed → notify, wait 30 min, retry once ·
repeat buyer → tag · inactive → campaign-candidate tag + follow-up · website
order → stock check → confirmation → wait for confirmation (1 day) → book, track
and tag, or else follow up. A recipe whose prerequisite is missing (courier,
channel) is kept as a draft, and the blocker is returned.

## API, RBAC, audit

- Read (`order.view`): `GET /automation/catalog`, `/workflows`, `/workflows/{id}`,
  `/executions` (paginated, filter by workflow or status), `/executions/{id}` (steps and
  history), `/metrics` (runs, completed, failed, waiting, average duration, top
  failure reasons), `/recipes`.
- Build (`automation.manage`, a new permission held by owner and manager): create,
  save draft, publish, switch on/off, `POST /workflows/{id}/preview` (reads only),
  `POST /workflows/{id}/run` (a real run that requires `confirm: true`), install
  recipes. Enabling or publishing re-validates, and a booking step needs the
  publisher to hold `order.book`.
- Operate (`order.write`): retry a failed run from its failed step, cancel a queued
  or waiting run.
- Runs execute with the publisher's authority, which is re-checked before every step.
- Audited: workflow created, changed, published, enabled and disabled; test run;
  run retried and cancelled.
- Responses carry codes only: no raw exceptions, provider bodies or credentials.

## Clients

- Web `/automation`: tiles, workflows, recipes, run history, failures with a
  retry/cancel drawer, tasks. `/automation/new` and `/automation/{id}` hold the
  structured WHEN/IF/THEN builder (add action, delay, event wait, branch; AND/OR
  groups), the preview, the confirmed test run and published versions.
  `npm test` checks that every backend trigger, action, field and wait has English
  and Bangla copy.
- Mobile (Settings → Automation): workflow list with on/off and 7-day status,
  failures with reason and retry, and one-tap recipes that need no setup. Editing
  stays on the web.

## Deploy notes

- Migration `a34001` (from `a33001`): two new tables, additive nullable/defaulted
  columns, and a data step that publishes existing rules and renames run statuses.
- The worker gains `scan_segment_entries` (daily, 22:10 UTC = 04:10 Dhaka).
  `dispatch_automation` is unchanged in schedule.
