"""Committing a large import in the background.

A five-thousand-row import creates five thousand orders through the *normal*
order service — customer normalisation, stock, the financial ledger, every
invariant. That is minutes of work, which is far longer than an HTTP request
should be held open, and a client that times out mid-commit and retries is
exactly how a shop ends up with two of everything.

So a large import is enqueued instead. Three properties make the handoff safe:

**One job per import, claimed by a state transition.** The request marks the
batch ``COMMITTING`` before enqueueing, and the job refuses any batch that is
already ``COMMITTED``. A duplicate delivery — ARQ retries, a double tap, a
redis hiccup — finds nothing left to do rather than committing twice.

**The row selection is the idempotency.** ``ImportService.commit`` only ever
takes rows still marked ``READY`` or ``WARNING``. A row that was created has
moved to ``CREATED``, so a resumed or repeated run skips it. There is no
separate "have I done this?" bookkeeping that could disagree with the rows.

**A crash costs one chunk.** The commit flushes every
``COMMIT_CHECKPOINT_ROWS``, so a worker that dies has already durably recorded
what it created, and the next run picks up from there.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from app.core.clock import utc_now
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger, log_duration
from app.db.session import system_session
from app.imports.models import ImportBatch, ImportStatus

__all__ = ["JOB_NAME", "commit_import_job", "sweep_stuck_imports"]

log = get_logger(__name__)

JOB_NAME = "imports:commit"

#: A commit that has been running longer than this is treated as abandoned and
#: offered for resumption. Generous, because a genuinely large import on a busy
#: database is slow, and resuming one that is still running would be worse than
#: waiting: both workers would walk the same rows.
STUCK_COMMIT_MINUTES = 30


async def commit_import_job(
    ctx: dict[str, Any] | None = None, *, import_id: str | uuid.UUID = ""
) -> dict[str, int]:
    """Commit one import, in the worker.

    Returns counts rather than raising for an import that has nothing to do:
    a duplicate delivery is a normal event, not a failure, and treating it as
    one would fill the error log with successes.
    """
    if not import_id:
        return {"created": 0, "skipped": 1}

    identifier = uuid.UUID(str(import_id))

    async with system_session(f"worker: {JOB_NAME}") as session:
        batch = (
            await session.execute(sa.select(ImportBatch).where(ImportBatch.id == identifier))
        ).scalar_one_or_none()

        if batch is None:
            log.warning("import job for an import that does not exist", extra=_ctx(identifier))
            return {"created": 0, "skipped": 1}

        if batch.import_status is ImportStatus.COMMITTED:
            # A duplicate delivery. The row selection would find nothing
            # anyway; returning early keeps it out of the error path.
            return {"created": 0, "skipped": 1}

        token = set_context(
            RequestContext(
                trace_id=uuid.uuid4().hex,
                tenant_id=batch.tenant_id,
                actor_type=ActorType.SYSTEM,
                job_name=JOB_NAME,
            )
        )
        try:
            from app.imports.service import build_import_service

            service = build_import_service(session)
            with log_duration(log, JOB_NAME, import_id=str(identifier)):
                committed = await service.commit(identifier)
            await session.flush()
            return {"created": committed.created_count, "skipped": 0}
        except Exception as exc:
            # The batch stays in COMMITTING with its checkpointed progress, so
            # the sweep below can offer it again. Marking it FAILED here would
            # throw away rows that were genuinely created.
            log.error(
                "import commit failed",
                extra={**_ctx(identifier), "error": type(exc).__name__},
            )
            return {"created": 0, "errors": 1}
        finally:
            clear_context(token)


async def sweep_stuck_imports(ctx: dict[str, Any] | None = None) -> dict[str, int]:
    """Find commits that stopped part-way and let them be resumed.

    A worker killed mid-commit leaves a batch in ``COMMITTING``. Nothing is
    wrong with the rows it already created — they are ``CREATED`` and will be
    skipped — but the batch needs re-enqueueing or a seller sees it stuck
    forever with no explanation.

    This only *reports* them and records why. Re-running the commit is a
    deliberate act, because a batch that keeps dying halfway is a bug to look
    at rather than a thing to retry forever.
    """
    cutoff = utc_now() - _stuck_delta()
    stopped: list[tuple[uuid.UUID, uuid.UUID]] = []

    async with system_session(f"worker: {JOB_NAME}:sweep") as session:
        stuck = list(
            (
                await session.execute(
                    sa.select(ImportBatch).where(
                        ImportBatch.status == str(ImportStatus.COMMITTING),
                        ImportBatch.started_at.is_not(None),
                        ImportBatch.started_at < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )

        for batch in stuck:
            batch.failure_reason = (
                "This import stopped part-way. The rows it already created are "
                "safe and will not be created again — run it again to finish "
                "the rest."
            )
            log.warning(
                "import stuck in COMMITTING",
                extra={
                    **_ctx(batch.id),
                    "processed": batch.processed_count,
                    "row_count": batch.row_count,
                },
            )
            stopped.append((batch.tenant_id, batch.id))
        await session.flush()

    # After the reason is committed, and per shop on a tenant-scoped session:
    # the uploader hears once that the import did not finish.
    for tenant_id, import_id in stopped:
        await _raise_import_alert(tenant_id, import_id)

    return {"stuck": len(stuck)}


async def _raise_import_alert(tenant_id: uuid.UUID, import_id: uuid.UUID) -> None:
    from app.db.session import session_scope
    from app.notifications.smart import SmartAlerts

    token = set_context(
        RequestContext(
            trace_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_type=ActorType.SYSTEM,
            job_name=f"{JOB_NAME}:alert",
        )
    )
    try:
        async with session_scope() as session:
            await SmartAlerts(session).import_alert(import_id)
    except Exception as exc:
        log.warning(
            "import alert could not be raised",
            extra={**_ctx(import_id), "error": type(exc).__name__},
        )
    finally:
        clear_context(token)


def _stuck_delta():
    from datetime import timedelta

    return timedelta(minutes=STUCK_COMMIT_MINUTES)


def _ctx(import_id: uuid.UUID) -> dict[str, str]:
    return {"job_name": JOB_NAME, "import_id": str(import_id)}
