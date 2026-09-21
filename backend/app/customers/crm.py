"""CRM queries compose existing order, RTO and profit truth, within one shop."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from app.analytics.customer_segments import (
    HIGH_VALUE_MIN_CUSTOMERS,
    INACTIVE_DAYS,
    NEW_DAYS,
    REPEAT_MIN_ORDERS,
    REPEAT_MIN_OUTCOMES,
    Segment,
)
from app.analytics.rto import (
    COMPLETED_STATUSES,
    MIN_RATE_SAMPLE,
    RTO_STATUSES,
    ParcelOutcome,
    RtoService,
    _settled_at,
    statuses_for,
)
from app.common.audit import record_audit
from app.common.money import _quantize_to_int
from app.common.pagination import Cursor, Page, apply_cursor
from app.consignments.models import Consignment
from app.core.clock import utc_now
from app.core.context import current_context, require_tenant_id
from app.core.errors import NotFoundError, ValidationError
from app.customers.crm_models import (
    CustomerActivity,
    CustomerFollowUp,
    CustomerTag,
    CustomerTagLink,
)
from app.customers.models import Customer
from app.customers.service import CustomerService
from app.orders.models import Order, OrderStatus
from app.profit.models import ProfitQuality, ProfitSnapshot
from app.tenants.models import Tenant, TenantUser
from app.tenants.roles import Permission, has_permission
from app.users.models import User


class CrmService:
    def __init__(self, db: AsyncSession, customers: CustomerService) -> None:
        self.db = db
        self.customers = customers
        self.tenant = require_tenant_id()

    def aggregates(self) -> tuple[Subquery, Subquery, Subquery, Subquery]:
        """Grouped once per query; never aggregate separately for each row."""
        terminal = (*COMPLETED_STATUSES, *statuses_for(ParcelOutcome.LOST))
        has_parcel = sa.exists().where(
            Consignment.order_id == Order.id, Consignment.tenant_id == self.tenant
        )
        pending_parcel = sa.exists().where(
            Consignment.order_id == Order.id,
            Consignment.tenant_id == self.tenant,
            Consignment.status.not_in(terminal),
        )
        active = sa.and_(
            Order.status.not_in([OrderStatus.COMPLETED, OrderStatus.CANCELLED]),
            sa.or_(~has_parcel, pending_parcel),
        )
        orders = (
            sa.select(
                Order.customer_id.label("customer_id"),
                sa.func.count().label("orders"),
                sa.func.min(Order.created_at).label("first"),
                sa.func.max(Order.created_at).label("last"),
                sa.func.sum(
                    Order.subtotal_paisa - Order.discount_paisa + Order.delivery_fee_paisa
                ).label("order_value"),
                sa.func.sum(
                    sa.case(
                        (active, 1),
                        else_=0,
                    )
                ).label("active"),
            )
            .where(Order.tenant_id == self.tenant)
            .group_by(Order.customer_id)
            .subquery()
        )
        delivered = statuses_for(ParcelOutcome.DELIVERED, ParcelOutcome.PARTIAL)
        parcels = (
            sa.select(
                Order.customer_id.label("customer_id"),
                sa.func.sum(sa.case((Consignment.status.in_(delivered), 1), else_=0)).label(
                    "delivered"
                ),
                sa.func.sum(sa.case((Consignment.status.in_(RTO_STATUSES), 1), else_=0)).label(
                    "rto"
                ),
                sa.func.sum(
                    sa.case((Consignment.status.in_(COMPLETED_STATUSES), 1), else_=0)
                ).label("completed"),
                sa.func.sum(sa.case((Consignment.status.in_(terminal), 1), else_=0)).label(
                    "settled"
                ),
            )
            .join(Order, Order.id == Consignment.order_id)
            .where(
                Order.tenant_id == self.tenant,
                Consignment.tenant_id == self.tenant,
            )
            .group_by(Order.customer_id)
            .subquery()
        )
        # Only ACTUAL snapshots contribute measurable money. Incomplete coverage
        # is returned explicitly; estimates and missing inputs are never called facts.
        money = (
            sa.select(
                Order.customer_id.label("customer_id"),
                sa.func.sum(
                    sa.case(
                        (Consignment.status.in_(delivered), ProfitSnapshot.realized_revenue_paisa),
                        else_=0,
                    )
                ).label("revenue"),
                sa.func.sum(ProfitSnapshot.contribution_profit_paisa).label("profit"),
                sa.func.count().label("measured_parcels"),
                sa.func.count(
                    sa.distinct(sa.case((Consignment.status.in_(delivered), Order.id)))
                ).label("measured_orders"),
            )
            .select_from(ProfitSnapshot)
            .join(Order, Order.id == ProfitSnapshot.order_id)
            .join(
                Consignment,
                Consignment.id == ProfitSnapshot.consignment_id,
            )
            .where(
                Order.tenant_id == self.tenant,
                ProfitSnapshot.tenant_id == self.tenant,
                Consignment.tenant_id == self.tenant,
                ProfitSnapshot.is_current.is_(True),
                ProfitSnapshot.quality == ProfitQuality.ACTUAL,
                Consignment.status.in_(terminal),
            )
            .group_by(Order.customer_id)
            .subquery()
        )
        due = (
            sa.select(
                CustomerFollowUp.customer_id.label("customer_id"),
                sa.func.min(CustomerFollowUp.due_at).label("next_due"),
            )
            .where(
                CustomerFollowUp.tenant_id == self.tenant, CustomerFollowUp.completed_at.is_(None)
            )
            .group_by(CustomerFollowUp.customer_id)
            .subquery()
        )
        return orders, parcels, money, due

    async def listing(
        self,
        *,
        limit: int,
        cursor: Cursor | None = None,
        search: str | None = None,
        segment: Segment | None = None,
        tag_id: uuid.UUID | None = None,
        min_orders: int | None = None,
        max_orders: int | None = None,
        last_from: datetime | None = None,
        last_until: datetime | None = None,
        flag: str | None = None,
        customer_id: uuid.UUID | None = None,
        money_allowed: bool = False,
    ) -> dict:
        o, p, m, f = self.aggregates()
        zero = sa.func.coalesce
        paying = (
            sa.select(m.c.revenue)
            .join(Customer, Customer.id == m.c.customer_id)
            .where(
                Customer.tenant_id == self.tenant,
                Customer.deleted_at.is_(None),
                m.c.revenue > 0,
            )
            .subquery()
        )
        revenue_sum, sample = (
            await self.db.execute(
                sa.select(sa.func.sum(paying.c.revenue), sa.func.count()).select_from(paying)
            )
        ).one()
        threshold = (
            (int(revenue_sum) + sample - 1) // sample
            if sample >= HIGH_VALUE_MIN_CUSTOMERS
            else None
        )
        now = utc_now()
        rules = {
            Segment.NEW: o.c.first >= now - timedelta(days=NEW_DAYS),
            Segment.REPEAT: zero(o.c.orders, 0) >= REPEAT_MIN_ORDERS,
            Segment.INACTIVE: o.c.last < now - timedelta(days=INACTIVE_DAYS),
            Segment.SUCCESSFUL_REPEAT: zero(p.c.delivered, 0) >= REPEAT_MIN_OUTCOMES,
            Segment.REPEATED_RTO: sa.and_(
                zero(p.c.rto, 0) >= REPEAT_MIN_OUTCOMES, zero(p.c.completed, 0) >= MIN_RATE_SAMPLE
            ),
            Segment.FOLLOW_UP_DUE: f.c.next_due <= now,
            Segment.ACTIVE_ORDERS: zero(o.c.active, 0) > 0,
            Segment.HIGH_VALUE: m.c.revenue >= threshold
            if threshold and money_allowed
            else sa.false(),
        }
        stmt = self.customers.search_query(search).where(Customer.tenant_id == self.tenant)
        for query in (o, p, m, f):
            stmt = stmt.outerjoin(query, query.c.customer_id == Customer.id)
        stmt = stmt.add_columns(
            zero(o.c.active, 0).label("active"),
            zero(o.c.order_value, 0).label("order_value"),
            m.c.revenue,
            m.c.profit,
            zero(m.c.measured_parcels, 0).label("measured_parcels"),
            zero(m.c.measured_orders, 0).label("measured_orders"),
            zero(p.c.settled, 0).label("completed"),
            f.c.next_due,
            *(sa.func.coalesce(rule, sa.false()).label(str(key)) for key, rule in rules.items()),
        )
        if customer_id:
            stmt = stmt.where(Customer.id == customer_id)
        if segment:
            stmt = stmt.where(rules[segment])
        if tag_id:
            stmt = stmt.where(
                sa.exists().where(
                    CustomerTagLink.customer_id == Customer.id,
                    CustomerTagLink.tenant_id == self.tenant,
                    CustomerTagLink.tag_id == tag_id,
                    CustomerTagLink.tag_id == CustomerTag.id,
                    CustomerTag.tenant_id == self.tenant,
                    CustomerTag.archived.is_(False),
                )
            )
        if min_orders is not None:
            stmt = stmt.where(zero(o.c.orders, 0) >= min_orders)
        if max_orders is not None:
            stmt = stmt.where(zero(o.c.orders, 0) <= max_orders)
        if last_from:
            stmt = stmt.where(o.c.last >= last_from)
        if last_until:
            stmt = stmt.where(o.c.last <= last_until)
        if flag:
            stmt = stmt.where(Customer.flag == flag)
        stmt = (
            apply_cursor(stmt, Customer, cursor)
            .order_by(Customer.created_at.desc(), Customer.id.desc())
            .limit(limit + 1)
        )
        rows = list((await self.db.execute(stmt)).all())
        ids = [row[0].id for row in rows]
        histories = await RtoService(self.db).customer_counts(ids)
        tags: dict[uuid.UUID, list[dict]] = {key: [] for key in ids}
        if ids:
            for cid, tag in await self.db.execute(
                sa.select(CustomerTagLink.customer_id, CustomerTag)
                .join(CustomerTag, CustomerTag.id == CustomerTagLink.tag_id)
                .where(CustomerTagLink.customer_id.in_(ids), CustomerTag.archived.is_(False))
                .order_by(CustomerTag.name)
            ):
                tags[cid].append({"id": tag.id, "name": tag.name})
        from app.api.v1.customers import _to_response

        def serialize(row: sa.Row[Any]) -> dict:
            customer = row[0]
            data = row._mapping
            result = _to_response(customer, histories[customer.id]).model_dump()
            measured_orders = data["measured_orders"]
            # PostgreSQL SUM(bigint) returns Decimal; keep the public money
            # contract as integer paisa on both supported databases.
            revenue = int(data["revenue"]) if data["revenue"] is not None else None
            profit = int(data["profit"]) if data["profit"] is not None else None
            result.update(
                realized_revenue_paisa=revenue if money_allowed else None,
                is_repeat_buyer=histories[customer.id].order_count >= REPEAT_MIN_ORDERS,
                active_orders=data["active"],
                tags=tags[customer.id],
                segments=[str(key) for key in rules if data[str(key)]],
                next_follow_up_at=data["next_due"],
                value={
                    "total_order_value_paisa": int(data["order_value"]),
                    "delivered_revenue_paisa": revenue,
                    "measured_profit_paisa": profit,
                    "average_delivered_order_paisa": _quantize_to_int(
                        Decimal(data["revenue"]) / measured_orders
                    )
                    if measured_orders
                    else None,
                    "measured_orders": measured_orders,
                    "measured_parcels": data["measured_parcels"],
                    "completed_parcels": data["completed"],
                }
                if money_allowed
                else None,
            )
            return result

        # Page.build needs the customer key, while serialization also needs aggregates.
        by_id = {row[0].id: row for row in rows}
        page = (
            Page[dict]
            .build(
                [row[0] for row in rows], limit=limit, serializer=lambda c: serialize(by_id[c.id])
            )
            .model_dump()
        )
        page["definitions"] = {
            "new_days": NEW_DAYS,
            "inactive_days": INACTIVE_DAYS,
            "repeat_min_orders": REPEAT_MIN_ORDERS,
            "rto_min_completed": MIN_RATE_SAMPLE,
            "repeat_min_outcomes": REPEAT_MIN_OUTCOMES,
            "high_value_min_customers": HIGH_VALUE_MIN_CUSTOMERS,
            "high_value_threshold_paisa": threshold if money_allowed else None,
        }
        return page

    async def activity(
        self,
        customer_id: uuid.UUID,
        kind: str,
        *,
        text: str | None = None,
        subject_id: uuid.UUID | None = None,
    ) -> CustomerActivity:
        row = CustomerActivity(
            customer_id=customer_id,
            kind=kind,
            text=text,
            actor_id=current_context().user_id,
            subject_id=subject_id,
        )
        self.db.add(row)
        await self.db.flush()
        await record_audit(
            self.db,
            f"crm.{kind.lower()}",
            entity_type="customer",
            entity_id=customer_id,
            context={"activity_id": str(row.id)},
        )
        return row

    async def create_tag(self, name: str) -> CustomerTag:
        await self.db.scalar(sa.select(Tenant.id).where(Tenant.id == self.tenant).with_for_update())
        name = name.strip()
        if not name:
            raise ValidationError("Tag cannot be empty")
        key = name.casefold()
        tag = await self.db.scalar(sa.select(CustomerTag).where(CustomerTag.name_key == key))
        if tag is None:
            tag = CustomerTag(name=name, name_key=key)
            self.db.add(tag)
        else:
            tag.archived = False
        await self.db.flush()
        await record_audit(self.db, "crm.tag_saved", entity_type="customer_tag", entity_id=tag.id)
        return tag

    async def bulk_tag(self, ids: list[uuid.UUID], tag_id: uuid.UUID, remove: bool) -> None:
        tag = await self.db.get(CustomerTag, tag_id)
        if tag is None or tag.archived:
            raise NotFoundError("Tag not found")
        # Lock in stable order: concurrent bulk actions cannot insert duplicate links.
        customers = list(
            (
                await self.db.scalars(
                    sa.select(Customer)
                    .where(Customer.id.in_(ids), Customer.deleted_at.is_(None))
                    .order_by(Customer.id)
                    .with_for_update()
                )
            ).all()
        )
        if len(customers) != len(set(ids)):
            raise NotFoundError("Customer not found")
        links = {
            link.customer_id: link
            for link in (
                await self.db.scalars(
                    sa.select(CustomerTagLink).where(
                        CustomerTagLink.customer_id.in_(ids), CustomerTagLink.tag_id == tag_id
                    )
                )
            ).all()
        }
        if not remove:
            counts = dict(
                (
                    await self.db.execute(
                        sa.select(CustomerTagLink.customer_id, sa.func.count())
                        .join(CustomerTag, CustomerTag.id == CustomerTagLink.tag_id)
                        .where(
                            CustomerTagLink.customer_id.in_(ids), CustomerTag.archived.is_(False)
                        )
                        .group_by(CustomerTagLink.customer_id)
                    )
                )
                .tuples()
                .all()
            )
            if any(
                counts.get(customer.id, 0) >= 50 and customer.id not in links
                for customer in customers
            ):
                raise ValidationError("A customer can have up to 50 active tags")
        for customer in customers:
            link = links.get(customer.id)
            if remove and link:
                await self.db.delete(link)
            elif not remove and not link:
                self.db.add(CustomerTagLink(customer_id=customer.id, tag_id=tag_id))
            else:
                continue
            await self.activity(
                customer.id,
                "TAG_REMOVED" if remove else "TAG_ADDED",
                text=tag.name,
                subject_id=tag.id,
            )

    async def create_followup(
        self, customer_id: uuid.UUID, text: str, due_at: datetime, assignee_id: uuid.UUID | None
    ) -> CustomerFollowUp:
        await self.customers.get(customer_id)
        if assignee_id:
            member = await self.db.scalar(
                sa.select(TenantUser).where(
                    TenantUser.user_id == assignee_id, TenantUser.is_active.is_(True)
                )
            )
            if member is None or not has_permission(member.role, Permission.ORDER_WRITE):
                raise ValidationError("Assignee must be an active operational member of this shop")
        row = CustomerFollowUp(
            customer_id=customer_id,
            text=text,
            due_at=due_at,
            assignee_id=assignee_id,
            created_by=current_context().user_id,
        )
        self.db.add(row)
        await self.db.flush()
        await self.activity(customer_id, "FOLLOW_UP_CREATED", subject_id=row.id)
        return row

    async def complete_followup(
        self, customer_id: uuid.UUID, followup_id: uuid.UUID, completed: bool
    ) -> CustomerFollowUp:
        await self.customers.get(customer_id)
        row = await self.db.scalar(
            sa.select(CustomerFollowUp)
            .where(CustomerFollowUp.id == followup_id, CustomerFollowUp.customer_id == customer_id)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError("Follow-up not found")
        if bool(row.completed_at) != completed:
            row.completed_at = utc_now() if completed else None
            row.completed_by = current_context().user_id if completed else None
            await self.activity(
                customer_id,
                "FOLLOW_UP_COMPLETED" if completed else "FOLLOW_UP_REOPENED",
                subject_id=row.id,
            )
        return row

    async def timeline(
        self,
        customer_id: uuid.UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        notes_only: bool = False,
        cursor_kind: str | None = None,
    ) -> dict:
        await self.customers.get(customer_id)
        actor = sa.cast(sa.null(), CustomerActivity.actor_id.type)
        activity = sa.select(
            CustomerActivity.id,
            CustomerActivity.created_at,
            CustomerActivity.kind,
            CustomerActivity.text,
            CustomerActivity.actor_id,
            CustomerActivity.subject_id.label("order_id"),
        ).where(
            CustomerActivity.tenant_id == self.tenant, CustomerActivity.customer_id == customer_id
        )
        if notes_only:
            query = activity.where(CustomerActivity.kind.in_(["NOTE", "NOTE_LEGACY"])).subquery()
        else:
            orders = sa.select(
                Order.id,
                Order.created_at,
                sa.literal("ORDER_CREATED"),
                Order.order_number,
                actor,
                Order.id,
            ).where(Order.tenant_id == self.tenant, Order.customer_id == customer_id)
            cancelled = sa.select(
                Order.id,
                Order.cancelled_at,
                sa.literal("ORDER_CANCELLED"),
                Order.order_number,
                actor,
                Order.id,
            ).where(
                Order.tenant_id == self.tenant,
                Order.customer_id == customer_id,
                Order.cancelled_at.is_not(None),
            )
            parcels = (
                sa.select(
                    Consignment.id,
                    _settled_at(),
                    sa.case(
                        (Consignment.status.in_(RTO_STATUSES), "ORDER_RTO"),
                        (
                            Consignment.status.in_(statuses_for(ParcelOutcome.PARTIAL)),
                            "ORDER_PARTIAL_DELIVERED",
                        ),
                        else_="ORDER_DELIVERED",
                    ),
                    Order.order_number,
                    actor,
                    Order.id,
                )
                .join(Order, Order.id == Consignment.order_id)
                .where(
                    Order.tenant_id == self.tenant,
                    Consignment.tenant_id == self.tenant,
                    Order.customer_id == customer_id,
                    Consignment.status.in_(COMPLETED_STATUSES),
                )
            )
            query = sa.union_all(activity, orders, cancelled, parcels).subquery()
        stmt = sa.select(query)
        if cursor:
            stmt = stmt.where(
                sa.or_(
                    query.c.created_at < cursor.created_at,
                    sa.and_(query.c.created_at == cursor.created_at, query.c.id < cursor.id),
                    sa.and_(
                        query.c.created_at == cursor.created_at,
                        query.c.id == cursor.id,
                        query.c.kind < (cursor_kind or ""),
                    ),
                )
            )
        stmt = stmt.order_by(
            query.c.created_at.desc(), query.c.id.desc(), query.c.kind.desc()
        ).limit(limit + 1)
        rows = list((await self.db.execute(stmt)).all())
        names = await self.actor_names([row.actor_id for row in rows if row.actor_id])
        page = (
            Page[dict]
            .build(
                rows,
                limit=limit,
                serializer=lambda row: {
                    **dict(row._mapping),
                    "actor_name": names.get(row.actor_id),
                    "order_id": row.order_id if row.kind.startswith("ORDER_") else None,
                },
            )
            .model_dump()
        )

        if page["next_cursor"]:
            page["next_cursor"] += "." + rows[limit - 1].kind
        return page

    async def actor_names(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
        if not ids:
            return {}
        return dict(
            (
                await self.db.execute(
                    sa.select(User.id, User.display_name)
                    .join(TenantUser, TenantUser.user_id == User.id)
                    .where(TenantUser.tenant_id == self.tenant, User.id.in_(ids))
                )
            )
            .tuples()
            .all()
        )
