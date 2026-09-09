"""Offline mutation sync.

Master spec sections 37 and 38.

Three rules shape this service.

**Every mutation is idempotent.** A device on a bad connection resends its
queue; the second send must return the first send's result, not create a second
order. Enforced by the unique ``(tenant_id, mutation_id)`` constraint plus the
stored result.

**Conflicts are reported, never resolved silently.** Section 128: a rare
editable-field conflict shows the seller their version and the server's. This
service returns both and applies neither.

**Server-authoritative fields are not accepted from a client.** Section 37 lists
courier state, payment state and provider identifiers as server-owned. A sync
payload that tries to set an order's status to something a courier decides is
rejected, not applied — otherwise a client bug could mark a parcel delivered and
create a receivable for money nobody collected.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.core.clock import ensure_utc
from app.core.errors import AppError, ErrorCode, ValidationError
from app.core.logging import get_logger
from app.customers.models import Customer
from app.customers.service import CustomerService
from app.orders.models import Order, OrderStatus
from app.orders.service import OrderDraft, OrderItemDraft, OrderService
from app.products.models import Product, StockMovementReason, StockMovementSource
from app.products.service import ProductService, StockAdjustment, StockService
from app.sync.models import (
    MutationOperation,
    MutationStatus,
    SyncEntity,
    SyncMutation,
)

__all__ = ["SYNC_PAGE_SIZE", "ChangeEntry", "MutationOutcome", "SyncService"]

log = get_logger(__name__)

#: Rows per changes page. Small enough for a slow connection to finish a page.
SYNC_PAGE_SIZE = 100

#: Order statuses a client may set offline.
#:
#: Deliberately excludes FULFILLMENT_STARTED and COMPLETED: both follow from a
#: courier outcome, which no offline device can know. Section 37 makes
#: courier-derived fields server-authoritative.
CLIENT_SETTABLE_ORDER_STATUS = frozenset(
    {
        OrderStatus.DRAFT,
        OrderStatus.CONFIRMED,
        OrderStatus.PACKED,
        OrderStatus.CANCELLED,
    }
)


@dataclass(slots=True)
class MutationOutcome:
    """What happened to one submitted mutation."""

    mutation_id: uuid.UUID
    status: MutationStatus
    entity_id: uuid.UUID | None = None
    server_version: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    server_state: dict[str, Any] | None = None

    def to_result(self) -> dict[str, Any]:
        return {
            "mutation_id": str(self.mutation_id),
            "status": str(self.status),
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "server_version": self.server_version,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(slots=True)
class ChangeEntry:
    """One record the device should pull."""

    entity_type: str
    entity_id: uuid.UUID
    updated_at: datetime
    version: int | None = None
    deleted: bool = False
    data: dict[str, Any] | None = None


def encode_sync_cursor(updated_at: datetime, entity_id: uuid.UUID) -> str:
    payload = {"t": ensure_utc(updated_at).isoformat(), "i": str(entity_id)}
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode_sync_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(payload["t"]), uuid.UUID(payload["i"])
    except (KeyError, ValueError, TypeError, binascii.Error) as exc:
        raise ValidationError("Malformed sync cursor") from exc


class SyncService:
    """Applies queued client mutations and serves the change feed."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        products: ProductService,
        orders: OrderService,
        customers: CustomerService,
    ) -> None:
        self._db = session
        self._products = products
        self._orders = orders
        self._customers = customers
        self._stock = StockService(session)

    # ---------------------------------------------------------------- push --

    async def push(
        self,
        mutations: list[dict[str, Any]],
        *,
        device_id: uuid.UUID | None = None,
    ) -> list[MutationOutcome]:
        """Apply a batch of queued mutations, in order.

        Each is applied in its own savepoint so one rejected mutation does not
        roll back the ones before it. A device with a single bad record still
        gets everything else through.
        """
        outcomes: list[MutationOutcome] = []

        for payload in mutations:
            try:
                outcome = await self._apply_one(payload, device_id=device_id)
            except Exception as error:
                log.warning(
                    "sync mutation failed",
                    extra={"error_type": type(error).__name__},
                )
                outcome = MutationOutcome(
                    mutation_id=uuid.UUID(str(payload.get("mutation_id"))),
                    status=MutationStatus.REJECTED,
                    error_code=ErrorCode.INTERNAL_ERROR,
                    error_message=str(error)[:300],
                )
            outcomes.append(outcome)

        await self._db.flush()
        return outcomes

    async def _apply_one(
        self, payload: dict[str, Any], *, device_id: uuid.UUID | None
    ) -> MutationOutcome:
        mutation_id = uuid.UUID(str(payload["mutation_id"]))
        entity_id = uuid.UUID(str(payload["entity_id"]))

        # Replay check first: a resent batch must not re-execute anything.
        existing = (
            await self._db.execute(
                sa.select(SyncMutation).where(SyncMutation.mutation_id == mutation_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Replay the stored outcome verbatim. `existing.entity_id` is the
            # id the *device* minted; the server id lives in the stored result,
            # and returning the wrong one would leave the device unable to
            # match its queued record to the row that was actually created.
            stored_entity_id = existing.result.get("entity_id")
            return MutationOutcome(
                mutation_id=mutation_id,
                status=MutationStatus.DUPLICATE,
                entity_id=uuid.UUID(stored_entity_id) if stored_entity_id else existing.entity_id,
                server_version=existing.result.get("server_version"),
                error_code=existing.error_code,
                error_message=existing.error_message,
            )

        try:
            entity_type = SyncEntity(str(payload["entity_type"]))
            operation = MutationOperation(str(payload["operation"]))
        except ValueError:
            return await self._record(
                mutation_id=mutation_id,
                entity_type=str(payload.get("entity_type", "")),
                entity_id=entity_id,
                operation=str(payload.get("operation", "")),
                payload=payload.get("payload", {}),
                device_id=device_id,
                outcome=MutationOutcome(
                    mutation_id=mutation_id,
                    status=MutationStatus.REJECTED,
                    entity_id=entity_id,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    # Courier booking and payouts are deliberately absent from
                    # SyncEntity; queueing one would be pretending an external
                    # action succeeded offline (section 62.17).
                    error_message=(f"{payload.get('entity_type')} cannot be synced from a device"),
                ),
            )

        body = dict(payload.get("payload") or {})
        base_version = payload.get("base_version")
        client_timestamp = payload.get("client_timestamp")

        try:
            outcome = await self._dispatch(entity_type, operation, entity_id, body, base_version)
        except AppError as error:
            outcome = MutationOutcome(
                mutation_id=mutation_id,
                status=MutationStatus.REJECTED,
                entity_id=entity_id,
                error_code=str(error.code),
                error_message=error.message_en,
            )
        outcome.mutation_id = mutation_id

        return await self._record(
            mutation_id=mutation_id,
            entity_type=str(entity_type),
            entity_id=entity_id,
            operation=str(operation),
            payload=body,
            device_id=device_id,
            base_version=base_version,
            client_timestamp=client_timestamp,
            outcome=outcome,
        )

    async def _dispatch(
        self,
        entity_type: SyncEntity,
        operation: MutationOperation,
        entity_id: uuid.UUID,
        body: dict[str, Any],
        base_version: int | None,
    ) -> MutationOutcome:
        match entity_type:
            case SyncEntity.ORDER:
                return await self._apply_order(operation, entity_id, body, base_version)
            case SyncEntity.PRODUCT:
                return await self._apply_product(operation, entity_id, body)
            case SyncEntity.CUSTOMER:
                return await self._apply_customer(operation, entity_id, body)
            case SyncEntity.STOCK_ADJUSTMENT:
                return await self._apply_stock(entity_id, body)

    # -------------------------------------------------------------- orders --

    async def _apply_order(
        self,
        operation: MutationOperation,
        entity_id: uuid.UUID,
        body: dict[str, Any],
        base_version: int | None,
    ) -> MutationOutcome:
        if operation is MutationOperation.CREATE:
            order, _ = await self._orders.create(
                OrderDraft(
                    phone=str(body.get("phone", "")),
                    customer_name=body.get("customer_name"),
                    address=body.get("address"),
                    district=body.get("district"),
                    area=body.get("area"),
                    items=[
                        OrderItemDraft(
                            product_id=uuid.UUID(item["product_id"])
                            if item.get("product_id")
                            else None,
                            name=item.get("name"),
                            quantity=int(item.get("quantity", 1)),
                            unit_price_paisa=item.get("unit_price_paisa"),
                            discount_paisa=int(item.get("discount_paisa", 0)),
                        )
                        for item in body.get("items", [])
                    ],
                    cod_amount_paisa=body.get("cod_amount_paisa"),
                    note=body.get("note"),
                    source_text=body.get("source_text"),
                    # The client id is the entity id, which is what makes a
                    # replayed create resolve to the same order.
                    client_id=entity_id,
                ),
                check_duplicates=False,
            )
            return MutationOutcome(
                mutation_id=entity_id,
                status=MutationStatus.APPLIED,
                entity_id=order.id,
                server_version=order.version,
            )

        order = await self._orders.get(entity_id)

        if base_version is not None and base_version != order.version:
            # Section 128: the seller is shown both versions and chooses.
            return MutationOutcome(
                mutation_id=entity_id,
                status=MutationStatus.CONFLICT,
                entity_id=order.id,
                server_version=order.version,
                server_state=self._order_state(order),
            )

        if operation is MutationOperation.DELETE:
            await self._orders.transition(
                order.id, OrderStatus.CANCELLED, reason=body.get("reason", "Cancelled offline")
            )
            return MutationOutcome(
                mutation_id=entity_id,
                status=MutationStatus.APPLIED,
                entity_id=order.id,
                server_version=order.version,
            )

        status_value = body.get("status")
        if status_value is not None:
            target = OrderStatus(str(status_value))
            if target not in CLIENT_SETTABLE_ORDER_STATUS:
                return MutationOutcome(
                    mutation_id=entity_id,
                    status=MutationStatus.REJECTED,
                    entity_id=order.id,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=(
                        f"{target} follows from a courier outcome and cannot be set from a device"
                    ),
                )

        updated = await self._orders.update(
            order.id,
            items=[
                OrderItemDraft(
                    product_id=uuid.UUID(item["product_id"]) if item.get("product_id") else None,
                    name=item.get("name"),
                    quantity=int(item.get("quantity", 1)),
                    unit_price_paisa=item.get("unit_price_paisa"),
                    discount_paisa=int(item.get("discount_paisa", 0)),
                )
                for item in body["items"]
            ]
            if "items" in body
            else None,
            cod_amount_paisa=body.get("cod_amount_paisa"),
            note=body.get("note"),
            address=body.get("address"),
            district=body.get("district"),
            area=body.get("area"),
        )

        if status_value is not None:
            updated = await self._orders.transition(updated.id, OrderStatus(str(status_value)))

        return MutationOutcome(
            mutation_id=entity_id,
            status=MutationStatus.APPLIED,
            entity_id=updated.id,
            server_version=updated.version,
        )

    @staticmethod
    def _order_state(order: Order) -> dict[str, Any]:
        """The server's version of the fields a client may edit."""
        return {
            "id": str(order.id),
            "version": order.version,
            "status": order.status,
            "cod_amount_paisa": order.cod_amount_paisa,
            "note": order.note,
            "delivery_address_raw": order.delivery_address_raw,
            "updated_at": order.updated_at.isoformat(),
        }

    # ------------------------------------------------------------ products --

    async def _apply_product(
        self, operation: MutationOperation, entity_id: uuid.UUID, body: dict[str, Any]
    ) -> MutationOutcome:
        if operation is MutationOperation.CREATE:
            existing = await self._db.get(Product, entity_id)
            if existing is not None:
                return MutationOutcome(
                    mutation_id=entity_id,
                    status=MutationStatus.APPLIED,
                    entity_id=existing.id,
                )
            product = await self._products.create(
                name=str(body.get("name", "")).strip(),
                sku=body.get("sku"),
                description=body.get("description"),
                cost_paisa=int(body.get("cost_paisa", 0)),
                default_selling_price_paisa=int(body.get("default_selling_price_paisa", 0)),
                opening_stock=int(body.get("opening_stock", 0)),
            )
            return MutationOutcome(
                mutation_id=entity_id,
                status=MutationStatus.APPLIED,
                entity_id=product.id,
            )

        if operation is MutationOperation.DELETE:
            product = await self._products.update(entity_id, archived=True)
            return MutationOutcome(
                mutation_id=entity_id,
                status=MutationStatus.APPLIED,
                entity_id=product.id,
            )

        product = await self._products.update(
            entity_id,
            name=body.get("name"),
            sku=body.get("sku"),
            description=body.get("description"),
            cost_paisa=body.get("cost_paisa"),
            default_selling_price_paisa=body.get("default_selling_price_paisa"),
            low_stock_threshold=body.get("low_stock_threshold"),
            is_active=body.get("is_active"),
        )
        return MutationOutcome(
            mutation_id=entity_id, status=MutationStatus.APPLIED, entity_id=product.id
        )

    # ----------------------------------------------------------- customers --

    async def _apply_customer(
        self, operation: MutationOperation, entity_id: uuid.UUID, body: dict[str, Any]
    ) -> MutationOutcome:
        if operation is MutationOperation.CREATE:
            customer, _ = await self._customers.get_or_create(
                phone=str(body.get("phone", "")),
                name=body.get("name"),
                address=body.get("address"),
            )
            return MutationOutcome(
                mutation_id=entity_id,
                status=MutationStatus.APPLIED,
                entity_id=customer.id,
            )

        from app.customers.models import CustomerFlag

        flag = body.get("flag")
        customer = await self._customers.update(
            entity_id,
            name=body.get("name"),
            notes=body.get("notes"),
            flag=CustomerFlag(str(flag)) if flag else None,
            flag_reason=body.get("flag_reason"),
        )
        return MutationOutcome(
            mutation_id=entity_id, status=MutationStatus.APPLIED, entity_id=customer.id
        )

    # --------------------------------------------------------------- stock --

    async def _apply_stock(self, entity_id: uuid.UUID, body: dict[str, Any]) -> MutationOutcome:
        reason = StockMovementReason(str(body.get("reason", "MANUAL_ADJUSTMENT")))
        if reason not in (
            StockMovementReason.MANUAL_ADJUSTMENT,
            StockMovementReason.OPENING,
            StockMovementReason.DAMAGED_WRITE_OFF,
        ):
            return MutationOutcome(
                mutation_id=entity_id,
                status=MutationStatus.REJECTED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message=f"{reason} is produced by the system, not by a device",
            )

        movement = await self._stock.record_movement(
            StockAdjustment(
                product_id=uuid.UUID(str(body["product_id"])),
                quantity_delta=int(body["quantity_delta"]),
                reason=reason,
                source=StockMovementSource.SELLER,
                note=body.get("note"),
            ),
            allow_negative=bool(body.get("allow_negative", False)),
        )
        return MutationOutcome(
            mutation_id=entity_id,
            status=MutationStatus.APPLIED,
            entity_id=movement.id,
        )

    # -------------------------------------------------------------- record --

    async def _record(
        self,
        *,
        mutation_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        operation: str,
        payload: dict[str, Any],
        device_id: uuid.UUID | None,
        outcome: MutationOutcome,
        base_version: int | None = None,
        client_timestamp: Any = None,
    ) -> MutationOutcome:
        """Persist the outcome so a replay returns it instead of re-executing."""
        parsed_timestamp: datetime | None = None
        if isinstance(client_timestamp, str):
            try:
                parsed_timestamp = ensure_utc(datetime.fromisoformat(client_timestamp))
            except ValueError:
                parsed_timestamp = None
        elif isinstance(client_timestamp, datetime):
            parsed_timestamp = ensure_utc(client_timestamp)

        self._db.add(
            SyncMutation(
                mutation_id=mutation_id,
                entity_type=entity_type,
                entity_id=entity_id,
                operation=operation,
                # Stored so support can explain what a device asked for.
                payload=payload,
                base_version=base_version,
                status=outcome.status,
                result=outcome.to_result(),
                error_code=outcome.error_code,
                error_message=outcome.error_message,
                client_timestamp=parsed_timestamp,
                device_id=device_id,
            )
        )

        if outcome.status is MutationStatus.CONFLICT:
            await record_audit(
                self._db,
                AuditAction.SYNC_CONFLICT_DETECTED,
                entity_type=entity_type.lower(),
                entity_id=entity_id,
                context={"mutation_id": str(mutation_id)},
            )
        elif outcome.status is MutationStatus.APPLIED:
            await record_audit(
                self._db,
                AuditAction.SYNC_MUTATION_APPLIED,
                entity_type=entity_type.lower(),
                entity_id=outcome.entity_id or entity_id,
                context={"operation": operation},
            )

        await self._db.flush()
        return outcome

    # -------------------------------------------------------------- changes --

    async def changes(
        self, *, cursor: str | None = None, limit: int = SYNC_PAGE_SIZE
    ) -> tuple[list[ChangeEntry], str | None, bool]:
        """Records changed since ``cursor``, oldest first.

        Ordered by ``(updated_at, id)`` ascending so a device can resume exactly
        where it stopped. Deleted records come back as tombstones rather than
        vanishing, or a device that missed the deletion would keep showing a row
        the seller removed (section 38).
        """
        after: tuple[datetime, uuid.UUID] | None = decode_sync_cursor(cursor) if cursor else None

        entries: list[ChangeEntry] = []
        # Typed as a concrete tuple so mypy resolves `updated_at` / `id` on each
        # model rather than on the shared declarative base.
        feeds: tuple[tuple[type[Order] | type[Product] | type[Customer], SyncEntity], ...] = (
            (Order, SyncEntity.ORDER),
            (Product, SyncEntity.PRODUCT),
            (Customer, SyncEntity.CUSTOMER),
        )
        for model, entity_type in feeds:
            stmt = sa.select(model).order_by(model.updated_at, model.id).limit(limit + 1)
            if after is not None:
                stmt = stmt.where(
                    sa.or_(
                        model.updated_at > after[0],
                        sa.and_(model.updated_at == after[0], model.id > after[1]),
                    )
                )
            for raw in (await self._db.execute(stmt)).scalars().all():
                # A select over a union of models resolves to the declarative
                # base for the type checker; the narrowing is what makes the
                # shared `id` / `updated_at` columns visible.
                row = cast("Order | Product | Customer", raw)
                entries.append(
                    ChangeEntry(
                        entity_type=str(entity_type),
                        entity_id=row.id,
                        updated_at=row.updated_at,
                        version=getattr(row, "version", None),
                        # Orders soft-delete; products archive. Either way the
                        # device is told to drop the record rather than keep
                        # showing something the seller removed.
                        deleted=getattr(row, "deleted_at", None) is not None
                        or getattr(row, "archived_at", None) is not None,
                    )
                )

        entries.sort(key=lambda entry: (entry.updated_at, str(entry.entity_id)))
        has_more = len(entries) > limit
        visible = entries[:limit]

        next_cursor = (
            encode_sync_cursor(visible[-1].updated_at, visible[-1].entity_id)
            if visible and has_more
            else None
        )
        return visible, next_cursor, has_more
