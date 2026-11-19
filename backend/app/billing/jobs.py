"""Outbox handlers for billing topics (V4 WP2.5).

``billing.manual_payment_reviewed`` → the seller's ``BILLING_PAYMENT_REVIEWED``
notification, written in the shop's own tenant scope (the dispatcher has put
the event's tenant into the context). Deduplicated per submission and outcome,
so a retried event notifies once.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.models import BillingPaymentSubmission, ManualPaymentState
from app.common.outbox import OutboxEvent, OutboxTopic
from app.core.clock import business_date
from app.core.logging import get_logger
from app.db.session import session_scope
from app.worker.jobs import register_handler

__all__: list[str] = []

log = get_logger("app.billing")


@register_handler(OutboxTopic.BILLING_MANUAL_PAYMENT_REVIEWED)
async def _on_manual_payment_reviewed(_session: AsyncSession, event: OutboxEvent) -> None:
    from app.notifications.models import NotificationKind, Severity
    from app.notifications.service import NotificationService
    from app.notifications.templates import render
    from app.tenants.roles import Permission

    payload = event.payload or {}
    submission_id = uuid.UUID(str(payload["submission_id"]))
    async with session_scope() as db:
        row = await db.get(BillingPaymentSubmission, submission_id)
        if row is None or row.state == ManualPaymentState.SUBMITTED:
            log.warning(
                "manual payment event without a reviewed row",
                extra={"submission_id": str(submission_id), "tenant_id": str(event.tenant_id)},
            )
            return
        params = {
            "state": row.state,
            "plan": row.plan_code,
            "months": row.months,
            "valid_until": (
                business_date(at=row.period_end).isoformat() if row.period_end else None
            ),
        }
        title, body = render(NotificationKind.BILLING_PAYMENT_REVIEWED, params, "en") or ("", "")
        approved = row.state == ManualPaymentState.APPROVED
        await NotificationService(db).notify(
            kind=NotificationKind.BILLING_PAYMENT_REVIEWED,
            severity=Severity.INFO if approved else Severity.ACTION,
            title=title,
            body=body,
            dedupe_key=f"manual_payment:{row.id}:{row.state}",
            entity_type="billing_payment_submission",
            entity_id=row.id,
            amount_paisa=row.amount_paisa,
            audience=Permission.BILLING_MANAGE,
            payload={"params": params, "target": {"route": "billing"}},
        )
    log.info(
        "manual payment reviewed",
        extra={"submission_id": str(submission_id), "state": params["state"]},
    )
