from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from app.automation.models import AutomationAttempt, AutomationExecution, AutomationRule
from app.automation.service import automation_depth, perform
from app.common.operation_lock import lock_shop
from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, current_context, set_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.session import session_scope, system_session
from app.tenants.models import TenantUser
from app.tenants.roles import Permission, has_permission


async def execute(tenant_id: uuid.UUID, execution_id: uuid.UUID) -> None:
    token = set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name="automation",
        )
    )
    depth = automation_depth.set(automation_depth.get() + 1)
    try:
        async with session_scope() as db:
            await lock_shop(db)
            execution = await db.scalar(
                sa.select(AutomationExecution)
                .where(AutomationExecution.id == execution_id)
                .with_for_update()
            )
            if (
                execution is None
                or execution.status not in {"PENDING", "RETRY"}
                or execution.next_attempt_at > utc_now()
            ):
                return
            rule = await db.scalar(
                sa.select(AutomationRule).where(AutomationRule.id == execution.rule_id)
            )
            if rule is None or not rule.enabled or rule.version != execution.rule_version:
                execution.status, execution.last_error = "SKIPPED", "RULE_DISABLED_OR_CHANGED"
                return
            member = await db.scalar(
                sa.select(TenantUser).where(
                    TenantUser.user_id == rule.created_by, TenantUser.is_active.is_(True)
                )
            )
            if member is None or not has_permission(member.role, Permission.SETTINGS_MANAGE):
                execution.status, execution.last_error = "SKIPPED", "RULE_OWNER_PERMISSION_REVOKED"
                return
            actor = set_context(replace(current_context(), user_id=rule.created_by))
            try:
                execution.attempts += 1
                # Atomic local action + execution receipt. Network messaging is
                # queued into its own durable delivery engine in this transaction.
                try:
                    async with db.begin_nested():
                        result = await perform(db, execution)
                    execution.status, execution.last_error, execution.result_id = (
                        "DONE",
                        None,
                        result,
                    )
                except (ConflictError, ValidationError, NotFoundError) as exc:
                    execution.status = "SKIPPED"
                    execution.last_error = str((exc.details or {}).get("blocker") or exc.code)
                except Exception as exc:
                    execution.status = "FAILED" if execution.attempts >= 5 else "RETRY"
                    execution.last_error = type(exc).__name__[:80]
                    execution.next_attempt_at = utc_now() + timedelta(
                        seconds=30 * 2**execution.attempts
                    )
                db.add(
                    AutomationAttempt(
                        execution_id=execution.id,
                        status=execution.status,
                        error=execution.last_error,
                    )
                )
                await db.flush()
            finally:
                clear_context(actor)
    finally:
        automation_depth.reset(depth)
        clear_context(token)


async def dispatch_automation(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    async with system_session("automation: due execution IDs") as db:
        rows = (
            await db.execute(
                sa.select(AutomationExecution.tenant_id, AutomationExecution.id)
                .where(
                    AutomationExecution.status.in_(["PENDING", "RETRY"]),
                    AutomationExecution.next_attempt_at <= utc_now(),
                )
                .order_by(AutomationExecution.next_attempt_at)
                .limit(50)
            )
        ).all()
    for tenant_id, execution_id in rows:
        await execute(tenant_id, execution_id)
    return {"processed": len(rows)}
