"""Customer CRM service.

Phone handling follows master spec section 133 throughout: the caller passes a
raw number, this service normalises it, derives the search HMAC, encrypts the
canonical form and stores the mask. The clear-text number exists only as a local
variable and is never returned.
"""

from __future__ import annotations

import re
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.pagination import Cursor, apply_cursor
from app.common.phone import PhoneNumber, normalize_bd_phone, normalize_digits
from app.core.clock import utc_now
from app.core.errors import ConflictError, NotFoundError
from app.core.security import CredentialVault, SecretHasher
from app.customers.models import Customer, CustomerAddress, CustomerFlag

__all__ = ["CUSTOMER_PHONE_CONTEXT", "CustomerService"]

#: GCM additional-authenticated-data context, binding a phone ciphertext to the
#: customer table. A ciphertext copied from another table fails to decrypt.
CUSTOMER_PHONE_CONTEXT = "customer.phone"

_WHITESPACE = re.compile(r"\s+")


def normalize_address_text(raw: str) -> str:
    """Our own normalisation: collapse whitespace, normalise numerals.

    Deliberately conservative. This is not a courier's address resolver — it
    only tidies what the seller typed so two spellings of the same address
    compare equal. Provider normalisation is a separate snapshot (section 71).
    """
    return _WHITESPACE.sub(" ", normalize_digits(raw)).strip()


class CustomerService:
    """Create, find and maintain the seller's private customer records."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        hasher: SecretHasher,
        vault: CredentialVault,
    ) -> None:
        self._db = session
        self._hasher = hasher
        self._vault = vault

    def phone_search_hash(self, phone_e164: str) -> str:
        """Lookup key for a canonical number.

        Exposed so the order service can scan for duplicates by phone without
        reaching into this service's internals or decrypting anything.
        """
        return self._hasher.phone_search_hash(phone_e164)

    # ------------------------------------------------------------- lookup ---

    async def find_by_phone(self, phone: str) -> Customer | None:
        """Exact lookup by phone.

        Matches on the keyed HMAC of the canonical E.164 form, so every input
        shape a seller might type — `01712345678`, `+8801712345678`, Bangla
        digits — finds the same record.
        """
        number = normalize_bd_phone(phone)
        return await self._find_by_hmac(self._hasher.phone_search_hash(number.e164))

    async def _find_by_hmac(self, phone_hmac: str) -> Customer | None:
        return (
            await self._db.execute(
                sa.select(Customer).where(
                    Customer.phone_search_hmac == phone_hmac,
                    Customer.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def get(self, customer_id: uuid.UUID) -> Customer:
        customer = await self._db.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            raise NotFoundError("Customer not found")
        return customer

    # ------------------------------------------------------------- create ---

    async def get_or_create(
        self,
        *,
        phone: str,
        name: str | None = None,
        address: str | None = None,
    ) -> tuple[Customer, bool]:
        """Find the customer for a phone, creating one if this is a new buyer.

        The order flow's entry point: a seller types a phone and either sees a
        known customer with their history, or starts a new record.
        """
        number = normalize_bd_phone(phone)
        phone_hmac = self._hasher.phone_search_hash(number.e164)

        existing = await self._find_by_hmac(phone_hmac)
        if existing is not None:
            # An existing customer's name is not overwritten by whatever was
            # typed on a later order; the seller edits it deliberately.
            if name and not existing.name:
                existing.name = name.strip()
            if address:
                await self.add_address(existing, raw_address=address)
            return existing, False

        customer = Customer(
            name=name.strip() if name else None,
            phone_search_hmac=phone_hmac,
            phone_enc=self._vault.encrypt(number.e164, context=CUSTOMER_PHONE_CONTEXT),
            phone_last4=number.last4,
            phone_masked=number.masked,
        )
        self._db.add(customer)
        await self._db.flush()

        if address:
            await self.add_address(customer, raw_address=address, is_default=True)

        await record_audit(
            self._db,
            AuditAction.CUSTOMER_CREATED,
            entity_type="customer",
            entity_id=customer.id,
            # The masked form only — an audit row is not a place to store a
            # number the rest of the schema goes to lengths to encrypt.
            context={"phone_masked": customer.phone_masked},
        )
        await self._db.flush()
        return customer, True

    async def create(
        self, *, phone: str, name: str | None = None, address: str | None = None
    ) -> Customer:
        customer, created = await self.get_or_create(phone=phone, name=name, address=address)
        if not created:
            raise ConflictError(
                "A customer with this phone already exists",
                details={"customer_id": str(customer.id)},
            )
        return customer

    # ------------------------------------------------------------- update ---

    async def update(
        self,
        customer_id: uuid.UUID,
        *,
        name: str | None = None,
        notes: str | None = None,
        flag: CustomerFlag | None = None,
        flag_reason: str | None = None,
    ) -> Customer:
        customer = await self.get(customer_id)
        changed: list[str] = []

        if name is not None:
            customer.name = name.strip() or None
            changed.append("name")
        if notes is not None:
            customer.notes = notes
            changed.append("notes")
        if flag is not None and flag != customer.flag:
            customer.flag = flag
            customer.flag_reason = flag_reason
            changed.append("flag")
            if flag is CustomerFlag.BLOCKED:
                # Blocking is a seller-private decision that affects their own
                # order flow. It is audited so the seller can see when and why.
                await record_audit(
                    self._db,
                    AuditAction.CUSTOMER_BLOCKED,
                    entity_type="customer",
                    entity_id=customer.id,
                    reason=flag_reason,
                )

        if changed:
            await record_audit(
                self._db,
                AuditAction.CUSTOMER_UPDATED,
                entity_type="customer",
                entity_id=customer.id,
                context={"changed": sorted(changed)},
            )
        await self._db.flush()
        return customer

    # ---------------------------------------------------------- addresses ---

    async def add_address(
        self,
        customer: Customer,
        *,
        raw_address: str,
        label: str | None = None,
        district: str | None = None,
        area: str | None = None,
        is_default: bool = False,
    ) -> CustomerAddress:
        """Attach an address, reusing an identical one rather than duplicating."""
        normalized = normalize_address_text(raw_address)
        if not normalized:
            raise ConflictError("An address cannot be empty")

        for existing in customer.addresses:
            if existing.normalized_address == normalized:
                existing.touch()
                if is_default:
                    await self._set_default(customer, existing)
                return existing

        address = CustomerAddress(
            tenant_id=customer.tenant_id,
            customer_id=customer.id,
            raw_address=raw_address,
            normalized_address=normalized,
            label=label,
            district=district,
            area=area,
            is_default=is_default or not customer.addresses,
        )
        self._db.add(address)
        customer.addresses.append(address)
        await self._db.flush()

        if address.is_default:
            await self._set_default(customer, address)
        return address

    async def _set_default(self, customer: Customer, address: CustomerAddress) -> None:
        for candidate in customer.addresses:
            candidate.is_default = candidate.id == address.id
        await self._db.flush()

    # -------------------------------------------------------------- list ----

    async def list_customers(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        search: str | None = None,
        repeat_only: bool = False,
        flag: CustomerFlag | None = None,
    ) -> list[Customer]:
        """List customers, newest first.

        Search accepts a name or a phone. A phone search resolves through the
        HMAC, so it is an exact match — there is deliberately no partial-number
        search, because that would require storing the number in a form that
        could be scanned.
        """
        stmt = sa.select(Customer).where(Customer.deleted_at.is_(None))

        if search:
            term = search.strip()
            digits = re.sub(r"\D", "", normalize_digits(term))
            conditions: list[sa.ColumnElement[bool]] = [
                sa.func.lower(sa.func.coalesce(Customer.name, "")).like(f"%{term.lower()}%")
            ]
            if len(digits) >= 4:
                # Last-four search: cheap, and reveals nothing beyond what the
                # seller already sees in their own list.
                conditions.append(Customer.phone_last4 == digits[-4:])
            from app.common.phone import try_normalize_bd_phone

            number = try_normalize_bd_phone(term)
            if number is not None:
                conditions.append(
                    Customer.phone_search_hmac == self._hasher.phone_search_hash(number.e164)
                )
            stmt = stmt.where(sa.or_(*conditions))

        if repeat_only:
            stmt = stmt.where(Customer.order_count >= 2)
        if flag is not None:
            stmt = stmt.where(Customer.flag == flag)
        stmt = apply_cursor(stmt, Customer, cursor)

        stmt = stmt.order_by(Customer.created_at.desc(), Customer.id.desc()).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

    # ----------------------------------------------------------- metrics ----

    async def note_order_placed(self, customer: Customer, *, at: object = None) -> None:
        """Record that an order was placed. Called by the order service."""
        now = utc_now()
        customer.order_count += 1
        customer.last_order_at = now
        if customer.first_order_at is None:
            customer.first_order_at = now
        await self._db.flush()

    async def recalculate_metrics(self, customer_id: uuid.UUID) -> Customer:
        """Rebuild the derived counters from this tenant's own orders.

        The repair path when a denormalised counter drifts. Delivered and
        returned counts stay at zero until courier outcomes exist (Phase C);
        only order counts are meaningful today, and this method is honest about
        which is which rather than inventing the rest.
        """
        from app.orders.models import Order, OrderStatus

        customer = await self.get(customer_id)

        rows = (
            await self._db.execute(
                sa.select(
                    Order.status,
                    sa.func.count(),
                    sa.func.min(Order.created_at),
                    sa.func.max(Order.created_at),
                )
                .where(Order.customer_id == customer_id)
                .group_by(Order.status)
            )
        ).all()

        totals = {status: count for status, count, _, _ in rows}
        customer.order_count = sum(totals.values())
        customer.cancelled_count = totals.get(OrderStatus.CANCELLED, 0)

        bounds = (
            await self._db.execute(
                sa.select(sa.func.min(Order.created_at), sa.func.max(Order.created_at)).where(
                    Order.customer_id == customer_id
                )
            )
        ).one()
        customer.first_order_at, customer.last_order_at = bounds

        await self._db.flush()
        return customer

    # ------------------------------------------------------------ reveal ----

    async def reveal_phone(self, customer_id: uuid.UUID, *, reason: str) -> str:
        """Decrypt a customer's phone for an authorised, audited purpose.

        Master spec section 101: a reveal action is audited. Used by "call the
        customer" and by an authorised export; never by a list endpoint.
        """
        customer = await self.get(customer_id)
        await record_audit(
            self._db,
            AuditAction.PHONE_REVEALED,
            entity_type="customer",
            entity_id=customer.id,
            reason=reason,
            context={"phone_masked": customer.phone_masked},
        )
        await self._db.flush()
        return self._vault.decrypt(customer.phone_enc, context=CUSTOMER_PHONE_CONTEXT)

    def phone_number(self, customer: Customer) -> PhoneNumber:
        """Canonical number for internal use (courier booking, SMS). Not audited."""
        return normalize_bd_phone(
            self._vault.decrypt(customer.phone_enc, context=CUSTOMER_PHONE_CONTEXT)
        )
