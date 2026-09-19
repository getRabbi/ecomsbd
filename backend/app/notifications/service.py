"""Creating and reading notifications.

Master spec sections 94 and 95. The whole module is shaped by one risk: a
notification centre that fills with things nobody needs to act on is a
notification centre nobody opens, and then the one alert that mattered — ৳8,950
of delivered parcels never paid for — goes unread with everything else.

So creation is deduplicated, low-value items are bundled rather than listed
individually, and anything that is merely *interesting* is `INFO`, which never
justifies interrupting the seller.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.pagination import Cursor, apply_cursor
from app.core.clock import business_date, utc_now
from app.core.context import require_tenant_id
from app.core.errors import NotFoundError
from app.db.tenancy import TENANT_CHECKED
from app.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationKind,
    Severity,
)
from app.notifications.templates import render
from app.tenants.roles import Permission

__all__ = ["BUNDLE_THRESHOLD", "NotificationService", "Viewer", "localized"]

#: More than this many low-priority items on one scan become a single bundle.
#: Section 95's own example is "5 parcels need attention" instead of five
#: separate notifications.
BUNDLE_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class Viewer:
    """One member reading the centre: what they may see and chose not to.

    The backend decides the audience. A notification addressed to one member
    is theirs alone; one raised for a permission is visible only to members
    whose role carries it; a muted category is hidden from its reader in the
    centre as well as never pushed to them.
    """

    user_id: uuid.UUID
    permissions: Collection[Permission]
    muted_categories: Collection[str] = field(default_factory=tuple)

    def clauses(self) -> list[sa.ColumnElement[bool]]:
        clauses: list[sa.ColumnElement[bool]] = [
            sa.or_(Notification.user_id.is_(None), Notification.user_id == self.user_id),
            sa.or_(
                Notification.audience == "",
                Notification.audience.in_([str(p) for p in self.permissions] or [""]),
            ),
        ]
        if self.muted_categories:
            clauses.append(Notification.category.not_in(list(self.muted_categories)))
        return clauses


def localized(notification: Notification, locale: str | None) -> tuple[str, str]:
    """The notification's title and body in the reader's language.

    V2.2 alerts are stored as facts and worded here; older rows, which only
    have their stored English, are returned as written.
    """
    payload = notification.payload or {}
    params = payload.get("params")
    if params is None and notification.kind == str(NotificationKind.WEEKLY_SUMMARY):
        params = payload
    rendered = render(notification.kind, params or {}, locale) if params else None
    return rendered or (notification.title, notification.body)


class NotificationService:
    """The only writer of notifications."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def notify(
        self,
        *,
        kind: NotificationKind,
        severity: Severity,
        title: str,
        body: str,
        dedupe_key: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        amount_paisa: int = 0,
        item_count: int = 1,
        payload: dict | None = None,
        occurred_at: datetime | None = None,
        category: NotificationCategory | None = None,
        audience: Permission | None = None,
        subject_key: str = "",
        user_id: uuid.UUID | None = None,
    ) -> Notification | None:
        """Create a notification, unless an identical one already exists.

        Returns ``None`` when it was deduplicated. The default dedupe key is
        the kind plus today's Dhaka date, so a daily scan produces one alert a
        day rather than one per run — a seller who opens the app three times
        before lunch should not find three copies of the same warning.
        """
        moment = occurred_at or utc_now()
        day = business_date(at=moment)
        key = dedupe_key if dedupe_key is not None else f"{kind}:{day.isoformat()}"

        if await self.exists(kind, key):
            return None

        from app.notifications.rules import category_for

        notification = Notification(
            kind=str(kind),
            severity=str(severity),
            category=str(category or category_for(kind)),
            audience=str(audience) if audience is not None else "",
            subject_key=subject_key,
            user_id=user_id,
            title=title,
            body=body,
            dedupe_key=key,
            entity_type=entity_type,
            entity_id=entity_id,
            amount_paisa=amount_paisa,
            item_count=item_count,
            business_date=day,
            payload=payload or {},
            created_at=moment,
        )
        self._db.add(notification)
        await self._db.flush()

        await record_audit(
            self._db,
            action=AuditAction.NOTIFICATION_SENT,
            entity_type="notification",
            entity_id=notification.id,
            context={
                "kind": str(kind),
                "severity": str(severity),
                "amount_paisa": amount_paisa,
            },
        )
        return notification

    async def exists(self, kind: NotificationKind, dedupe_key: str) -> bool:
        """Whether this exact notification has already been written.

        Exposed so a scheduled job can skip building an expensive payload —
        the Friday summary runs several aggregate queries — for something that
        :meth:`notify` would immediately deduplicate away.
        """
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(Notification)
            .where(
                Notification.kind == str(kind),
                Notification.dedupe_key == dedupe_key,
            )
        )
        return int(result.scalar_one()) > 0

    async def bundle(
        self,
        items: list[tuple[str, uuid.UUID]],
        *,
        title: str,
        body: str,
        severity: Severity = Severity.ACTION,
        amount_paisa: int = 0,
    ) -> Notification | None:
        """Roll several small items into one notification.

        Section 95: *"Do not send one push per parcel event… Bundle
        low-priority items."* Below the threshold the caller should notify
        individually, because three named parcels are more useful than
        "3 parcels need attention"; above it, the list stops being readable and
        the count is the useful part.
        """
        if not items:
            return None
        return await self.notify(
            kind=NotificationKind.BUNDLE,
            severity=severity,
            title=title,
            body=body,
            amount_paisa=amount_paisa,
            item_count=len(items),
            payload={
                "items": [
                    {"entity_type": entity_type, "entity_id": str(entity_id)}
                    for entity_type, entity_id in items[:50]
                ]
            },
        )

    # -------------------------------------------------------------- reading --

    async def list_notifications(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        unread_only: bool = False,
        severity: Severity | None = None,
        category: NotificationCategory | None = None,
        viewer: Viewer | None = None,
    ) -> list[Notification]:
        stmt = sa.select(Notification)
        if viewer is not None:
            stmt = stmt.where(*viewer.clauses())
        if category is not None:
            stmt = stmt.where(Notification.category == str(category))
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        if severity is not None:
            stmt = stmt.where(Notification.severity == str(severity))
        stmt = apply_cursor(stmt, Notification, cursor)
        stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(
            limit + 1
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def unread_count(self, *, viewer: Viewer | None = None) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(Notification)
            .where(
                Notification.read_at.is_(None),
                *(viewer.clauses() if viewer is not None else ()),
            )
        )
        return int(result.scalar_one())

    async def mark_read(
        self, notification_id: uuid.UUID, *, viewer: Viewer | None = None
    ) -> Notification:
        stmt = sa.select(Notification).where(Notification.id == notification_id)
        if viewer is not None:
            stmt = stmt.where(*viewer.clauses())
        notification = (await self._db.execute(stmt)).scalar_one_or_none()
        if notification is None:
            # Someone else's, or not visible to this role: indistinguishable
            # from not existing, on purpose.
            raise NotFoundError("Notification not found")
        if notification.read_at is None:
            notification.read_at = utc_now()
            await self._db.flush()
        return notification

    async def mark_all_read(self, *, viewer: Viewer | None = None) -> int:
        """Clear the badge.

        Returns how many were marked, so the UI can say what it did rather
        than silently emptying a list the seller was part-way through.

        The tenant filter is written out by hand. The session guards catch
        unscoped *reads*, but a bulk UPDATE goes through none of them, so
        without this line one seller tapping "mark all read" would clear every
        other seller's badge too.
        """
        table = cast("sa.Table", Notification.__table__)
        result = await self._db.execute(
            sa.update(table)
            .where(
                table.c.tenant_id == require_tenant_id(),
                table.c.read_at.is_(None),
                # Only what this reader can see: a finance member clearing
                # their badge must not mark a courier alert read for operators.
                *(
                    [table.c.id.in_(sa.select(Notification.id).where(*viewer.clauses()))]
                    if viewer is not None
                    else []
                ),
            )
            .values(read_at=utc_now())
            .execution_options(**{TENANT_CHECKED: True, "synchronize_session": False})
        )
        await self._db.flush()
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    async def since(self, day: date) -> list[Notification]:
        result = await self._db.execute(
            sa.select(Notification)
            .where(Notification.business_date >= day)
            .order_by(Notification.created_at.desc())
        )
        return list(result.scalars().all())
