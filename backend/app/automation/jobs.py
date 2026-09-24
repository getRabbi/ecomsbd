"""The automation engine: one dispatcher, one step per transaction.

A run advances one step at a time. Each step commits together with its
receipt (``AutomationStepRun``), so:

* a crash between steps resumes at the next step, never repeats the last one;
* a failed step rolls back its own local writes (a savepoint) and is retried
  from that step, with every earlier side effect left alone;
* delays and event waits are rows (``WAITING`` + ``next_attempt_at``), picked up
  by the existing ``dispatch_automation`` cron on the existing ARQ worker. There
  is no scheduler per workflow and nothing is held in memory across a restart.

Courier booking is the one step that cannot share a transaction: the V2 booking
service commits its attempt before calling the courier. It gets its own path
(``_book``) that decides from the order's parcels, not from memory, whether a
booking may be attempted at all.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import app.automation.triggers  # noqa: F401  (registers the V3.4 outbox handlers)
from app.analytics.customer_segments import INACTIVE_DAYS
from app.automation.actions import perform
from app.automation.facts import Facts, evaluate
from app.automation.models import (
    AutomationAttempt,
    AutomationExecution,
    AutomationRule,
    AutomationStepRun,
    AutomationWorkflowVersion,
)
from app.automation.schemas import is_acyclic
from app.automation.service import (
    Cause,
    automation_cause,
    automation_depth,
    can_manage,
    entry_holds,
    publisher_of,
)
from app.automation.triggers import wait_key
from app.common.operation_lock import lock_shop
from app.common.outbox import OutboxTopic, enqueue
from app.consignments.models import Consignment, ConsignmentStatus
from app.core.clock import ensure_utc, tenant_now, utc_now
from app.core.context import ActorType, RequestContext, clear_context, current_context, set_context
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.customers.crm_models import CustomerFollowUp
from app.db.session import session_scope, system_session
from app.orders.models import Order, OrderStatus
from app.tenants.models import Tenant
from app.tenants.roles import Permission, has_permission

log = get_logger(__name__)

RUNNABLE = ("QUEUED", "WAITING")
FINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED")
#: Automatic attempts of one step before it needs a person.
MAX_ATTEMPTS = 5
#: Steps one dispatcher call may advance a run, so one run cannot hog a tick.
STEPS_PER_CALL = 25
#: Parcel states that mean "a booking exists or may exist": never book again.
LIVE_OR_UNKNOWN = frozenset(
    {
        str(s)
        for s in ConsignmentStatus
        if s
        not in (ConsignmentStatus.CANCELLED, ConsignmentStatus.FAILED, ConsignmentStatus.NOT_BOOKED)
    }
)
CONFIRMED_OR_LATER = frozenset(
    {
        str(OrderStatus.CONFIRMED),
        str(OrderStatus.PACKED),
        str(OrderStatus.FULFILLMENT_STARTED),
        str(OrderStatus.COMPLETED),
    }
)
AMBIGUOUS = frozenset({str(ConsignmentStatus.BOOKING), str(ConsignmentStatus.BOOKING_UNKNOWN)})

CONTINUE, STOP, BOOK = "CONTINUE", "STOP", "BOOK"


def _code(exc: AppError) -> str:
    return str((exc.details or {}).get("blocker") or exc.code)[:80]


def _legacy_plan(execution: AutomationExecution) -> dict[str, Any]:
    """A V2 execution (no version row) as a one-step plan."""
    snapshot = execution.snapshot or {}
    return {
        "entry": "s1",
        "steps": {
            "s1": {
                "type": "action",
                "id": "s1",
                "action": snapshot.get("action"),
                "config": snapshot.get("config") or {},
                "next": None,
            }
        },
    }


def _subject(execution: AutomationExecution) -> dict[str, Any]:
    snapshot = execution.snapshot or {}
    return snapshot.get("subject") or snapshot


def _finish(execution: AutomationExecution, status: str, reason: str | None = None) -> None:
    execution.status, execution.last_error = status, reason
    execution.finished_at = utc_now()
    execution.wait_key = None
    if status == "FAILED" and execution.retryable is None:
        execution.retryable = False


def _log(
    db: AsyncSession,
    execution: AutomationExecution,
    step: str | None,
    status: str,
    error: str | None,
) -> None:
    db.add(AutomationAttempt(execution_id=execution.id, status=status, error=error, step_id=step))


async def _receipt(
    db: AsyncSession, execution: AutomationExecution, step: dict
) -> AutomationStepRun | None:
    return await db.scalar(
        sa.select(AutomationStepRun).where(
            AutomationStepRun.execution_id == execution.id,
            AutomationStepRun.step_id == step["id"],
        )
    )


async def _record(
    db: AsyncSession,
    execution: AutomationExecution,
    step: dict,
    status: str,
    *,
    result_id: str | None = None,
    outcome: str | None = None,
    receipt: AutomationStepRun | None = None,
) -> AutomationStepRun:
    row = receipt or await _receipt(db, execution, step)
    if row is None:
        row = AutomationStepRun(
            execution_id=execution.id, step_id=step["id"], kind=step["type"], status=status
        )
        db.add(row)
    row.status, row.result_id, row.outcome = status, result_id, outcome
    row.finished_at = utc_now() if status not in ("WAITING", "IN_FLIGHT") else None
    await db.flush()
    return row


def _advance_to(execution: AutomationExecution, target: str | None) -> str:
    execution.cursor, execution.attempts = target, 0
    execution.status, execution.wait_key, execution.wait_satisfied_at = "QUEUED", None, None
    execution.next_attempt_at = utc_now()
    if target is None:
        _finish(execution, "SUCCEEDED")
        return STOP
    return CONTINUE


def _fail(
    db: AsyncSession, execution: AutomationExecution, step: dict | None, code: str, retryable: bool
) -> str:
    execution.retryable = retryable
    _finish(execution, "FAILED", code)
    _log(db, execution, step["id"] if step else None, "FAILED", code)
    return STOP


# ------------------------------------------------------------ wait helpers ---


async def _wake_at(db: AsyncSession, execution: AutomationExecution, step: dict) -> datetime | None:
    now = utc_now()
    if step.get("mode", "duration") == "duration":
        return now + timedelta(minutes=int(step["minutes"]))
    if step["mode"] == "until_time":
        zone = await db.scalar(sa.select(Tenant.timezone).where(Tenant.id == execution.tenant_id))
        local = tenant_now(zone, at=now)
        hour, minute = (int(part) for part in step["time"].split(":"))
        target = local.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(
            days=int(step.get("day_offset") or 0)
        )
        if target <= local:
            target += timedelta(days=1)
        return ensure_utc(target)
    # followup_due: the due time of the follow-up an earlier step created.
    source = await db.scalar(
        sa.select(AutomationStepRun).where(
            AutomationStepRun.execution_id == execution.id,
            AutomationStepRun.step_id == step.get("step"),
            AutomationStepRun.status == "SUCCEEDED",
        )
    )
    if source is None or not source.result_id:
        return None
    due = await db.scalar(
        sa.select(CustomerFollowUp.due_at).where(CustomerFollowUp.id == uuid.UUID(source.result_id))
    )
    return ensure_utc(due) if due else None


async def _already_happened(db: AsyncSession, execution: AutomationExecution, step: dict) -> bool:
    """Whether what a wait is for is already true: no waiting for the past."""
    subject, event = _subject(execution), step["event"]
    order_id = subject.get("order_id")
    if event == "order.confirmed" and order_id:
        status = await db.scalar(sa.select(Order.status).where(Order.id == uuid.UUID(order_id)))
        return str(status) in CONFIRMED_OR_LATER
    if event in {"courier.booked", "order.delivered"} and order_id:
        statuses = {
            str(s)
            for s in (
                await db.scalars(
                    sa.select(Consignment.status).where(Consignment.order_id == uuid.UUID(order_id))
                )
            ).all()
        }
        if event == "order.delivered":
            return bool(statuses & {"DELIVERED", "PARTIAL_DELIVERED"})
        return bool(statuses & (LIVE_OR_UNKNOWN - AMBIGUOUS))
    if event == "followup.completed":
        created = (
            await db.scalars(
                sa.select(AutomationStepRun.result_id).where(
                    AutomationStepRun.execution_id == execution.id,
                    AutomationStepRun.kind == "action",
                    AutomationStepRun.status == "SUCCEEDED",
                )
            )
        ).all()
        ids = []
        for value in created:
            try:
                ids.append(uuid.UUID(str(value)))
            except ValueError:
                continue
        if not ids:
            return False
        open_rows = await db.scalar(
            sa.select(sa.func.count(CustomerFollowUp.id)).where(
                CustomerFollowUp.id.in_(ids), CustomerFollowUp.completed_at.is_(None)
            )
        )
        done = await db.scalar(
            sa.select(sa.func.count(CustomerFollowUp.id)).where(CustomerFollowUp.id.in_(ids))
        )
        return bool(done) and not open_rows
    return False  # a reply only counts if it comes after the wait began


# -------------------------------------------------------------- one step ---


async def _step(
    db: AsyncSession,
    execution: AutomationExecution,
    step: dict,
) -> str:
    kind = step["type"]
    receipt = await _receipt(db, execution, step)

    if kind == "action":
        if receipt is not None and receipt.status in ("SUCCEEDED", "SKIPPED"):
            return _advance_to(execution, step.get("next"))  # never repeat a done effect
        if step["action"] == "BOOK_COURIER":
            return BOOK
        try:
            async with db.begin_nested():
                status, result, outcome = await perform(db, execution, step)
                await _record(
                    db, execution, step, status, result_id=result, outcome=outcome, receipt=receipt
                )
        except (ValidationError, NotFoundError) as exc:
            await _record(db, execution, step, "FAILED", outcome=_code(exc))
            return _fail(db, execution, step, _code(exc), retryable=False)
        except ConflictError as exc:
            # A state the seller can change (a limit, a customer that moved).
            await _record(db, execution, step, "FAILED", outcome=_code(exc))
            return _fail(db, execution, step, _code(exc), retryable=True)
        except Exception as exc:
            return _transient(db, execution, step, exc)
        _log(db, execution, step["id"], status, outcome)
        execution.result_id = result
        return _advance_to(execution, step.get("next"))

    if kind == "delay":
        wake = await _wake_at(db, execution, step)
        if wake is None:
            await _record(db, execution, step, "FAILED", outcome="FOLLOWUP_NOT_FOUND")
            return _fail(db, execution, step, "FOLLOWUP_NOT_FOUND", retryable=False)
        await _record(
            db, execution, step, "SUCCEEDED", outcome=wake.isoformat()[:80], receipt=receipt
        )
        following = step.get("next")
        if following is None or wake <= utc_now():
            return _advance_to(execution, following)  # nothing after it, or already due
        # The cursor moves past the delay now, so waking up cannot wait twice.
        _advance_to(execution, following)
        execution.status, execution.next_attempt_at = "WAITING", wake
        _log(db, execution, step["id"], "WAITING", None)
        return STOP

    if kind == "wait_event":
        timeout_at = None
        if receipt is not None:
            timeout_at = ensure_utc(receipt.created_at) + timedelta(
                minutes=int(step["timeout_minutes"])
            )
        if execution.wait_satisfied_at is not None or await _already_happened(db, execution, step):
            await _record(db, execution, step, "SUCCEEDED", outcome="EVENT", receipt=receipt)
            _log(db, execution, step["id"], "SUCCEEDED", None)
            return _advance_to(execution, step.get("next"))
        if receipt is not None and timeout_at is not None and utc_now() >= timeout_at:
            await _record(db, execution, step, "TIMED_OUT", outcome="TIMEOUT", receipt=receipt)
            _log(db, execution, step["id"], "TIMED_OUT", None)
            if step.get("on_timeout") == "stop":
                execution.cursor = None
                _finish(execution, "SUCCEEDED")
                return STOP
            return _advance_to(execution, step.get("next"))
        key = wait_key(step["event"], _subject(execution))
        if key is None:
            await _record(db, execution, step, "FAILED", outcome="NO_SUBJECT")
            return _fail(db, execution, step, "NO_SUBJECT", retryable=False)
        if receipt is None:
            receipt = await _record(db, execution, step, "WAITING")
            timeout_at = ensure_utc(receipt.created_at) + timedelta(
                minutes=int(step["timeout_minutes"])
            )
            _log(db, execution, step["id"], "WAITING", None)
        execution.status, execution.wait_key = "WAITING", key
        execution.next_attempt_at = timeout_at or utc_now()
        return STOP

    if kind == "branch":
        holds, _trace = await evaluate(Facts(db, _subject(execution)), step.get("conditions"))
        await _record(
            db, execution, step, "SUCCEEDED", outcome="THEN" if holds else "ELSE", receipt=receipt
        )
        _log(db, execution, step["id"], "THEN" if holds else "ELSE", None)
        return _advance_to(execution, step.get("then_next") if holds else step.get("else_next"))

    return _fail(db, execution, step, "UNKNOWN_STEP", retryable=False)


def _transient(db: AsyncSession, execution: AutomationExecution, step: dict, exc: Exception) -> str:
    execution.attempts += 1
    code = f"TRANSIENT:{type(exc).__name__}"[:80]
    log.warning(
        "automation step failed",
        extra={"execution_id": str(execution.id), "step": step["id"], "error": type(exc).__name__},
    )
    if execution.attempts >= MAX_ATTEMPTS:
        return _fail(db, execution, step, code, retryable=True)
    execution.status, execution.last_error = "QUEUED", code
    execution.next_attempt_at = utc_now() + timedelta(seconds=30 * 2**execution.attempts)
    _log(db, execution, step["id"], "RETRY", code)
    return STOP


async def _load(
    db: AsyncSession, execution_id: uuid.UUID, *, statuses: tuple[str, ...] = RUNNABLE
) -> tuple[AutomationExecution, AutomationRule, dict[str, Any], uuid.UUID] | None:
    """Lock a due run and everything it needs; settle it if it may not go on."""
    await lock_shop(db)
    execution = await db.scalar(
        sa.select(AutomationExecution)
        .where(AutomationExecution.id == execution_id)
        .with_for_update()
    )
    if (
        execution is None
        or execution.status not in statuses
        or ensure_utc(execution.next_attempt_at) > utc_now()
    ):
        return None
    rule = await db.scalar(sa.select(AutomationRule).where(AutomationRule.id == execution.rule_id))
    version = (
        await db.scalar(
            sa.select(AutomationWorkflowVersion).where(
                AutomationWorkflowVersion.id == execution.version_id
            )
        )
        if execution.version_id
        else None
    )
    if rule is None or not rule.enabled:
        _finish(execution, "CANCELLED", "WORKFLOW_DISABLED")
        _log(db, execution, execution.cursor, "CANCELLED", "WORKFLOW_DISABLED")
        return None
    publisher = version.published_by if version else rule.created_by
    member = await publisher_of(db, publisher)
    if not can_manage(member):
        _finish(execution, "CANCELLED", "RULE_OWNER_PERMISSION_REVOKED")
        _log(db, execution, execution.cursor, "CANCELLED", "RULE_OWNER_PERMISSION_REVOKED")
        return None
    plan = version.plan if version else _legacy_plan(execution)
    if not is_acyclic(plan):
        _fail(db, execution, None, "INVALID_PLAN", retryable=False)
        return None
    if execution.started_at is None:
        execution.started_at = utc_now()
        if version is not None:
            holds, _ = await entry_holds(db, version, execution)
            if not holds:
                _finish(execution, "SKIPPED", "CONDITIONS_NOT_MET")
                _log(db, execution, None, "SKIPPED", "CONDITIONS_NOT_MET")
                return None
    return execution, rule, plan, publisher


def _as_actor(
    execution: AutomationExecution, rule: AutomationRule, publisher: uuid.UUID
) -> list[Any]:
    """Run a step with the publisher's authority and this run as the cause."""
    return [
        set_context(replace(current_context(), user_id=publisher)),
        automation_cause.set(
            Cause(
                execution_id=execution.id,
                rule_id=rule.id,
                depth=execution.depth,
                ancestry=tuple(execution.ancestry or ()),
            )
        ),
        automation_depth.set(execution.depth + 1),
    ]


def _reset(tokens: list[Any]) -> None:
    automation_depth.reset(tokens[2])
    automation_cause.reset(tokens[1])
    clear_context(tokens[0])


async def _advance(execution_id: uuid.UUID) -> str:
    async with session_scope() as db:
        loaded = await _load(db, execution_id)
        if loaded is None:
            return STOP
        execution, rule, plan, publisher = loaded
        if execution.cursor is None:
            _finish(execution, "SUCCEEDED")
            return STOP
        step = plan["steps"].get(execution.cursor)
        if step is None:
            return _fail(db, execution, None, "INVALID_PLAN", retryable=False)
        tokens = _as_actor(execution, rule, publisher)
        try:
            result = await _step(db, execution, step)
        finally:
            _reset(tokens)
        await db.flush()
        return result


# ---------------------------------------------------------- courier booking ---


async def _book(execution_id: uuid.UUID) -> str:
    """Book a courier for this run's order, at most once, ever.

    1. Look at the order's parcels. A live one means the order is booked (by a
       person or an earlier attempt): the step is done. A BOOKING or
       BOOKING_UNKNOWN one means nobody knows yet: the run stops, and booking
       recovery — not this workflow — settles it.
    2. Only with no parcel at all, mark the step IN_FLIGHT and commit.
    3. Call the V2 booking service, which commits its own attempt before the
       courier is called and maps a lost answer to BOOKING_UNKNOWN.
    4. Record what it said.
    """
    async with session_scope() as db:
        loaded = await _load(db, execution_id)
        if loaded is None:
            return STOP
        execution, rule, plan, publisher = loaded
        step = plan["steps"][execution.cursor or ""]
        receipt = await _receipt(db, execution, step)
        if receipt is not None and receipt.status in ("SUCCEEDED", "SKIPPED"):
            return _advance_to(execution, step.get("next"))
        member = await publisher_of(db, publisher)
        if member is None or not has_permission(member.role, Permission.ORDER_BOOK):
            await _record(db, execution, step, "FAILED", outcome="BOOKING_PERMISSION_REQUIRED")
            return _fail(db, execution, step, "BOOKING_PERMISSION_REQUIRED", retryable=False)
        order_id = uuid.UUID(str(_subject(execution).get("order_id")))
        parcels = (
            await db.execute(
                sa.select(Consignment.id, Consignment.status, Consignment.created_at)
                .where(Consignment.order_id == order_id)
                .order_by(Consignment.created_at.desc())
            )
        ).all()
        for parcel_id, status, _created in parcels:
            if str(status) in AMBIGUOUS:
                await _record(
                    db, execution, step, "FAILED", outcome="BOOKING_UNKNOWN", receipt=receipt
                )
                return _fail(db, execution, step, "BOOKING_UNKNOWN", retryable=False)
            if str(status) in LIVE_OR_UNKNOWN:
                await _record(
                    db,
                    execution,
                    step,
                    "SUCCEEDED",
                    result_id=str(parcel_id),
                    outcome="ALREADY_BOOKED",
                    receipt=receipt,
                )
                _log(db, execution, step["id"], "SUCCEEDED", "ALREADY_BOOKED")
                return _advance_to(execution, step.get("next"))
        if receipt is not None and receipt.status == "IN_FLIGHT":
            refused = [
                p
                for p in parcels
                if str(p[1]) == str(ConsignmentStatus.FAILED)
                and ensure_utc(p[2]) >= ensure_utc(receipt.updated_at)
            ]
            if refused:
                await _record(
                    db, execution, step, "FAILED", outcome="BOOKING_FAILED", receipt=receipt
                )
                return _fail(db, execution, step, "BOOKING_FAILED", retryable=True)
        await _record(db, execution, step, "IN_FLIGHT", receipt=receipt)
        execution.status = "RUNNING"
        _log(db, execution, step["id"], "IN_FLIGHT", None)
        provider = step["config"]["provider"]
        tokens = _as_actor(execution, rule, publisher)
    # Committed: the step is IN_FLIGHT. The booking gets its own session, since
    # the booking service commits in the middle of its work.
    outcome: str | None = None
    code: str | None = None
    consignment_id: str | None = None
    failure: Exception | None = None
    try:
        async with session_scope() as db:
            report = await _booking_service(db).book(order_id, provider=provider)
            item = report.items[0]
            outcome, code = item.outcome, item.error_code
            consignment_id = str(item.consignment_id) if item.consignment_id else None
    except AppError as exc:
        code, failure = _code(exc), exc
    except Exception as exc:
        failure = exc
    finally:
        _reset(tokens)
    async with session_scope() as db:
        await lock_shop(db)
        settled = await db.scalar(
            sa.select(AutomationExecution)
            .where(AutomationExecution.id == execution_id)
            .with_for_update()
        )
        if settled is None:
            return STOP
        execution = settled
        receipt = await _receipt(db, execution, step)
        if outcome == str(ConsignmentStatus.BOOKED):
            await _record(
                db, execution, step, "SUCCEEDED", result_id=consignment_id, receipt=receipt
            )
            _log(db, execution, step["id"], "SUCCEEDED", None)
            execution.result_id = consignment_id
            return _advance_to(execution, step.get("next"))
        if outcome == str(ConsignmentStatus.BOOKING_UNKNOWN):
            await _record(db, execution, step, "FAILED", outcome="BOOKING_UNKNOWN", receipt=receipt)
            return _fail(db, execution, step, "BOOKING_UNKNOWN", retryable=False)
        if outcome is not None or isinstance(failure, AppError):
            reason = (code or "BOOKING_FAILED")[:80]
            await _record(db, execution, step, "FAILED", outcome=reason, receipt=receipt)
            return _fail(
                db, execution, step, reason, retryable=not isinstance(failure, ValidationError)
            )
        # An unexpected error: the parcel check at the next attempt decides.
        execution.status = "QUEUED"
        return _transient(db, execution, step, failure or RuntimeError())


def _booking_service(db: AsyncSession) -> Any:
    from app.api.deps import get_vault
    from app.consignments.service import ConsignmentService
    from app.core.config import get_settings
    from app.couriers.accounts import CourierAccountService
    from app.couriers.booking import CourierBookingService
    from app.money.service import ReceivableService

    settings = get_settings()
    vault = get_vault(settings)
    return CourierBookingService(
        db,
        accounts=CourierAccountService(db, vault=vault),
        consignments=ConsignmentService(db, receivables=ReceivableService(db)),
        vault=vault,
        settings=settings,
    )


# ------------------------------------------------------------- entry points ---


async def execute(tenant_id: uuid.UUID, execution_id: uuid.UUID) -> None:
    """Advance one run as far as it can go now."""
    token = set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="automation",
        )
    )
    try:
        for _ in range(STEPS_PER_CALL):
            result = await _advance(execution_id)
            if result == BOOK:
                result = await _book(execution_id)
            if result != CONTINUE:
                break
    finally:
        clear_context(token)


async def dispatch_automation(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Due runs: new, retrying, woken by an event, or at the end of a wait."""
    async with system_session("automation: due execution IDs") as db:
        rows = (
            await db.execute(
                sa.select(AutomationExecution.tenant_id, AutomationExecution.id)
                .where(
                    AutomationExecution.status.in_(RUNNABLE),
                    AutomationExecution.next_attempt_at <= utc_now(),
                )
                .order_by(AutomationExecution.next_attempt_at)
                .limit(50)
            )
        ).all()
    for tenant_id, execution_id in rows:
        try:
            await execute(tenant_id, execution_id)
        except Exception as exc:  # one broken run must not stall the others
            log.exception(
                "automation run failed",
                extra={"execution_id": str(execution_id), "error": type(exc).__name__},
            )
    return {"processed": len(rows)}


async def scan_segment_entries(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Announce customers who became *inactive* since yesterday.

    Inactivity is the passage of time, so no transaction ever emits it; this is
    the one trigger that needs a scan. It runs daily, only for shops with a
    workflow listening, and each (customer, last order) is announced once.
    """
    async with system_session("automation: shops with segment workflows") as db:
        tenants = (
            await db.scalars(
                sa.select(AutomationRule.tenant_id)
                .where(
                    AutomationRule.enabled.is_(True),
                    AutomationRule.trigger == "customer.segment_entered",
                )
                .distinct()
            )
        ).all()
    announced = 0
    for tenant_id in tenants:
        token = set_context(
            RequestContext(
                trace_id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                actor_type=ActorType.SYSTEM,
                job_name="automation-segments",
            )
        )
        try:
            async with session_scope() as db:
                announced += await _announce_inactive(db, tenant_id)
        except Exception as exc:
            log.exception("segment scan failed", extra={"error": type(exc).__name__})
        finally:
            clear_context(token)
    return {"announced": announced}


async def _announce_inactive(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    from app.common.outbox import OutboxEvent

    now = utc_now()
    last = sa.func.max(Order.created_at)
    rows = (
        await db.execute(
            sa.select(Order.customer_id, last)
            .where(Order.deleted_at.is_(None), Order.customer_id.is_not(None))
            .group_by(Order.customer_id)
            .having(
                last < now - timedelta(days=INACTIVE_DAYS),
                last >= now - timedelta(days=INACTIVE_DAYS + 2),
            )
            .limit(500)
        )
    ).all()
    count = 0
    for customer_id, last_at in rows:
        key = f"segment:INACTIVE:{customer_id}:{ensure_utc(last_at).date().isoformat()}"
        seen = await db.scalar(sa.select(OutboxEvent.id).where(OutboxEvent.dedupe_key == key))
        if seen:
            continue
        await enqueue(
            db,
            OutboxTopic.SEGMENT_ENTERED,
            {"customer_id": str(customer_id), "segment": "INACTIVE"},
            tenant_id=tenant_id,
            dedupe_key=key,
        )
        count += 1
    return count
