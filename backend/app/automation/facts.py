"""Deterministic facts for workflow conditions.

Every fact is read from the system of record (orders, consignments, CRM,
messaging consent, products, integrations) at the moment a condition is
evaluated. Facts carried by the trigger event itself — the status an order
moved *to*, the segment a customer entered — take precedence for the entry
conditions, so a rule about "confirmed" means the confirmation that triggered
it, not whatever the order has become since.

Nothing here is a prediction or a score.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.network_models import NetworkBenchmarkCell
from app.analytics.rto import RtoService
from app.consignments.models import Consignment, ConsignmentStatus
from app.customers.crm_models import CustomerTag, CustomerTagLink
from app.customers.models import Customer
from app.customers.risk import assess
from app.integrations.models import IntegrationConnection, IntegrationEvent
from app.messaging.models import Conversation
from app.order_sources.models import ExternalOrder
from app.orders.models import Order, OrderItem
from app.products.models import Product, ProductVariant
from app.risk_providers.models import ExternalRiskLookup
from app.risk_providers.service import external_data_state

__all__ = ["Facts", "evaluate"]

#: Consignment states that are not a live parcel.
_NOT_LIVE = (str(ConsignmentStatus.CANCELLED), str(ConsignmentStatus.FAILED))


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


class Facts:
    """Lazily resolved facts about one run's subject, cached per evaluation."""

    def __init__(
        self, db: AsyncSession, subject: dict[str, Any], event_facts: dict[str, Any] | None = None
    ) -> None:
        self.db = db
        self.subject = subject
        self.event_facts = dict(event_facts or {})
        self._cache: dict[str, Any] = {}

    @property
    def order_id(self) -> uuid.UUID | None:
        return _uuid(self.subject.get("order_id"))

    @property
    def customer_id(self) -> uuid.UUID | None:
        return _uuid(self.subject.get("customer_id"))

    async def get(self, field: str) -> Any:
        if field in self.event_facts:
            return self.event_facts[field]
        if field not in self._cache:
            self._cache[field] = await self._read(field)
        return self._cache[field]

    async def _order(self) -> Order | None:
        if "_order" not in self._cache:
            self._cache["_order"] = (
                await self.db.scalar(
                    sa.select(Order).where(Order.id == self.order_id, Order.deleted_at.is_(None))
                )
                if self.order_id
                else None
            )
        return self._cache["_order"]

    async def _consignment(self) -> Consignment | None:
        if "_consignment" not in self._cache:
            self._cache["_consignment"] = (
                await self.db.scalar(
                    sa.select(Consignment)
                    .where(Consignment.order_id == self.order_id)
                    .order_by(
                        sa.case((Consignment.status.in_(_NOT_LIVE), 1), else_=0),
                        Consignment.created_at.desc(),
                    )
                    .limit(1)
                )
                if self.order_id
                else None
            )
        return self._cache["_consignment"]

    async def _read(self, field: str) -> Any:
        order = await self._order() if field in _ORDER_FIELDS else None
        if field in _ORDER_FIELDS and order is None:
            return None
        if field == "status":
            return str(order.status) if order else None
        if field == "channel":
            return str(order.channel) if order else None
        if field == "cod_amount_paisa":
            return order.cod_amount_paisa if order else None
        if field == "payment_method":
            return ("COD" if order.cod_amount_paisa > 0 else "PREPAID") if order else None
        if field in {"courier", "consignment_status"}:
            parcel = await self._consignment()
            if parcel is None:
                return None if field == "courier" else "NOT_BOOKED"
            return parcel.provider.lower() if field == "courier" else str(parcel.status)
        if field == "sku":
            rows = await self.db.scalars(
                sa.select(OrderItem.sku).where(
                    OrderItem.order_id == self.order_id, OrderItem.sku.is_not(None)
                )
            )
            return {str(sku).casefold() for sku in rows.all()}
        if field == "items_in_stock":
            return await self._items_in_stock()
        if field == "external_source":
            provider = await self.db.scalar(
                sa.select(IntegrationConnection.provider)
                .join(ExternalOrder, ExternalOrder.source_id == IntegrationConnection.source_id)
                .where(ExternalOrder.order_id == self.order_id)
                .limit(1)
            )
            if provider:
                return str(provider)
            return "API" if order is not None and str(order.channel) == "API" else "NONE"
        if field == "customer_tag":
            if self.customer_id is None:
                return None
            rows = await self.db.scalars(
                sa.select(CustomerTagLink.tag_id)
                .join(CustomerTag, CustomerTag.id == CustomerTagLink.tag_id)
                .where(
                    CustomerTagLink.customer_id == self.customer_id,
                    CustomerTag.archived.is_(False),
                )
            )
            return {str(tag) for tag in rows.all()}
        if field == "customer_segment":
            return await self._segments()
        if field == "customer_flag":
            if self.customer_id is None:
                return None
            flag = await self.db.scalar(
                sa.select(Customer.flag).where(Customer.id == self.customer_id)
            )
            return str(flag) if flag else None
        if field == "returned_parcels":
            if self.customer_id is None:
                return None
            return int(
                await self.db.scalar(
                    sa.select(sa.func.count(Consignment.id))
                    .join(Order, Order.id == Consignment.order_id)
                    .where(
                        Order.customer_id == self.customer_id,
                        Consignment.status == str(ConsignmentStatus.RETURNED),
                    )
                )
                or 0
            )
        if field in {"transactional_consent", "marketing_consent"}:
            if self.customer_id is None:
                return None
            column = (
                Conversation.consent
                if field == "transactional_consent"
                else Conversation.marketing_consent
            )
            found = await self.db.scalar(
                sa.select(Conversation.id)
                .where(
                    Conversation.customer_id == self.customer_id,
                    column.is_(True),
                    Conversation.undeliverable_at.is_(None),
                )
                .limit(1)
            )
            return found is not None
        if field == "stock_on_hand":
            variant = _uuid(self.subject.get("variant_id"))
            product = _uuid(self.subject.get("product_id"))
            if variant:
                return await self.db.scalar(
                    sa.select(ProductVariant.stock_on_hand).where(ProductVariant.id == variant)
                )
            if product:
                return await self.db.scalar(
                    sa.select(Product.stock_on_hand).where(Product.id == product)
                )
            return None
        if field in {"integration_provider", "sync_error_code"}:
            event_id = _uuid(self.subject.get("integration_event_id"))
            if event_id is None:
                return None
            row = (
                await self.db.execute(
                    sa.select(IntegrationEvent.provider, IntegrationEvent.code).where(
                        IntegrationEvent.id == event_id
                    )
                )
            ).first()
            if row is None:
                return None
            return row[0] if field == "integration_provider" else row[1]
        if field == "risk_state":
            if self.customer_id is None:
                return None
            history = await RtoService(self.db).customer_history(self.customer_id)
            return str(assess(history).state)
        if field == "external_data_state":
            if self.customer_id is None:
                return None
            return await external_data_state(self.db, self.customer_id)
        if field == "external_found":
            if self.customer_id is None:
                return None
            status = await self.db.scalar(
                sa.select(ExternalRiskLookup.status)
                .where(
                    ExternalRiskLookup.customer_id == self.customer_id,
                    ExternalRiskLookup.status.in_(("FOUND", "NOT_FOUND")),
                )
                .order_by(ExternalRiskLookup.checked_at.desc())
                .limit(1)
            )
            return None if status is None else status == "FOUND"
        if field == "network_rto_percent":
            return await self.db.scalar(
                sa.select(NetworkBenchmarkCell.value)
                .where(
                    NetworkBenchmarkCell.metric == "RTO_RATE",
                    NetworkBenchmarkCell.dimension == "ALL",
                    NetworkBenchmarkCell.status == "PUBLISHED",
                )
                .order_by(NetworkBenchmarkCell.period.desc())
                .limit(1)
            )
        return None

    async def _items_in_stock(self) -> bool | None:
        items = (
            await self.db.execute(
                sa.select(OrderItem.product_id, OrderItem.variant_id, OrderItem.quantity).where(
                    OrderItem.order_id == self.order_id
                )
            )
        ).all()
        for product_id, variant_id, quantity in items:
            if variant_id is not None:
                stock = await self.db.scalar(
                    sa.select(ProductVariant.stock_on_hand).where(ProductVariant.id == variant_id)
                )
            elif product_id is not None:
                row = (
                    await self.db.execute(
                        sa.select(Product.stock_on_hand, Product.stock_tracking_enabled).where(
                            Product.id == product_id
                        )
                    )
                ).first()
                if row is None or not row[1]:
                    continue  # untracked products are not judged
                stock = row[0]
            else:
                continue  # a free-text line has no stock to check
            if stock is None or stock < quantity:
                return False
        return True

    async def _segments(self) -> set[str] | None:
        if self.customer_id is None:
            return None
        from app.api.deps import get_hasher, get_vault
        from app.customers.crm import CrmService
        from app.customers.service import CustomerService

        crm = CrmService(self.db, CustomerService(self.db, hasher=get_hasher(), vault=get_vault()))
        stmt, rules, _ = await crm.filtered(customer_id=self.customer_id)
        row = (await self.db.execute(stmt)).mappings().first()
        if row is None:
            return set()
        return {str(key) for key in rules if row.get(str(key))}


_ORDER_FIELDS = frozenset(
    {
        "status",
        "channel",
        "cod_amount_paisa",
        "payment_method",
        "courier",
        "consignment_status",
        "sku",
        "items_in_stock",
        "external_source",
    }
)


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "is_true":
        return actual is True
    if op == "is_false":
        return actual is False
    if actual is None:
        # Unknown is never a match, not even for "not equal": a condition about
        # a parcel cannot pass for an order that has none by accident.
        return False
    if op in {"has", "not_has"}:
        present = str(expected).casefold() in {str(v).casefold() for v in actual}
        return present if op == "has" else not present
    if op in {"in", "not_in"}:
        inside = str(actual) in {str(v) for v in expected}
        return inside if op == "in" else not inside
    if op in {"gte", "lte"}:
        if type(actual) is not int or type(expected) is not int:
            return False
        return actual >= expected if op == "gte" else actual <= expected
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    return False


async def _set(facts: Facts, data: dict[str, Any]) -> tuple[list[bool], list[dict[str, Any]]]:
    results = [
        {
            "field": condition["field"],
            "op": condition["op"],
            "value": condition.get("value"),
            "matched": _compare(
                await facts.get(condition["field"]), condition["op"], condition.get("value")
            ),
        }
        for condition in data.get("conditions") or []
    ]
    return [bool(row["matched"]) for row in results], results


async def evaluate(facts: Facts, group: dict[str, Any] | None) -> tuple[bool, list[dict]]:
    """Whether a condition group holds, with a per-condition trace for previews.

    An empty group holds. ``all`` needs every condition and nested group;
    ``any`` needs at least one of them.
    """
    if not group:
        return True, []
    trace: list[dict] = []
    parts, rows = await _set(facts, group)
    trace += rows
    for nested in group.get("groups") or []:
        values, inner_rows = await _set(facts, nested)
        trace += inner_rows
        if values:
            parts.append(all(values) if nested.get("match", "all") == "all" else any(values))
    if not parts:
        return True, trace
    return (all(parts) if group.get("match", "all") == "all" else any(parts)), trace
