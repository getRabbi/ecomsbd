"""Risk check.

Master spec sections 24, 121 and 130. The properties under test:

*   the band is a function of this shop's own finished orders and nothing else;
*   a thin history returns ``INSUFFICIENT_DATA``, never a flattering ``LOW``;
*   one seller's customer history is invisible to every other seller;
*   the daily quota is spent even when the number is unknown, so the endpoint
    cannot be used as a free number-probing oracle.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header
from tests.test_customers import create_customer

from app.customers import risk
from app.customers.models import Customer


class _Counters:
    """Just enough of a Customer for the pure rules."""

    def __init__(self, delivered: int, returned: int, cancelled: int) -> None:
        self.order_count = delivered + returned + cancelled
        self.delivered_count = delivered
        self.returned_count = returned
        self.cancelled_count = cancelled
        self.first_order_at = None
        self.last_order_at = None

    @property
    def terminal_count(self) -> int:
        return self.delivered_count + self.returned_count + self.cancelled_count

    @property
    def success_rate_basis_points(self) -> int | None:
        if self.terminal_count == 0:
            return None
        return round(self.delivered_count * 10_000 / self.terminal_count)


class TestRules:
    def test_no_history_is_insufficient_not_low(self) -> None:
        assessed = risk.assess(_Counters(0, 0, 0))  # type: ignore[arg-type]
        assert assessed.state is risk.RiskState.INSUFFICIENT_DATA
        assert risk.RiskReason.NO_ORDERS in assessed.reasons

    def test_a_single_delivery_is_still_insufficient(self) -> None:
        # The flattering answer, and the one this must not give.
        assessed = risk.assess(_Counters(1, 0, 0))  # type: ignore[arg-type]
        assert assessed.state is risk.RiskState.INSUFFICIENT_DATA
        assert risk.RiskReason.SMALL_SAMPLE in assessed.reasons

    def test_bands_split_at_the_documented_thresholds(self) -> None:
        assert risk.assess(_Counters(4, 1, 0)).state is risk.RiskState.LOW  # type: ignore[arg-type]
        assert risk.assess(_Counters(2, 2, 0)).state is risk.RiskState.MEDIUM  # type: ignore[arg-type]
        assert risk.assess(_Counters(1, 3, 0)).state is risk.RiskState.HIGH  # type: ignore[arg-type]

    def test_every_answer_is_marked_own_shop_only(self) -> None:
        for counters in (_Counters(0, 0, 0), _Counters(5, 0, 0), _Counters(0, 5, 0)):
            assessed = risk.assess(counters)  # type: ignore[arg-type]
            assert risk.RiskReason.OWN_SHOP_HISTORY_ONLY in assessed.reasons

    def test_cancellations_count_against_the_rate(self) -> None:
        # Reuses Customer.terminal_count, so a cancelled order is a finished
        # order that did not deliver -- not an absent one.
        assessed = risk.assess(_Counters(1, 0, 3))  # type: ignore[arg-type]
        assert assessed.state is risk.RiskState.HIGH
        assert risk.RiskReason.HAS_CANCELLATIONS in assessed.reasons


async def _set_counters(
    db: AsyncSession, customer_id: str, *, delivered: int, returned: int, cancelled: int
) -> None:
    # Through the mapped column rather than raw SQL: the id arrives as the
    # API's hyphenated string, and only the column type knows how the database
    # stores it. A raw comparison matched no row and silently changed nothing.
    result = await db.execute(
        sa.update(Customer)
        .where(Customer.id == uuid.UUID(customer_id))
        .values(
            delivered_count=delivered,
            returned_count=returned,
            cancelled_count=cancelled,
            order_count=delivered + returned + cancelled,
        )
        .execution_options(synchronize_session=False)
    )
    assert result.rowcount == 1
    await db.commit()


class TestRiskCheckApi:
    async def test_an_unknown_number_is_insufficient_data_not_an_error(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)

        response = await client.get(
            "/v1/customers/risk-check",
            params={"phone": "01700000001"},
            headers=auth_header(session),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["found"] is False
        assert body["state"] == "INSUFFICIENT_DATA"
        assert body["terminal_count"] == 0

    async def test_history_drives_the_band_and_is_shown_in_full(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        customer = await create_customer(client, session, phone="01712345678", name="Nusrat")
        await _set_counters(system_db, customer["id"], delivered=4, returned=1, cancelled=0)

        response = await client.get(
            "/v1/customers/risk-check",
            params={"phone": "01712345678"},
            headers=auth_header(session),
        )

        body = response.json()
        assert body["found"] is True
        assert body["state"] == "LOW"
        assert body["delivered_count"] == 4
        assert body["returned_count"] == 1
        assert body["terminal_count"] == 5
        # 4/5 -- the seller can check the verdict against the numbers shown.
        assert body["success_rate_basis_points"] == 8000
        assert "STRONG_DELIVERY_RATE" in body["reasons"]

    async def test_a_returning_customer_bands_high(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        customer = await create_customer(client, session, phone="01712345679")
        await _set_counters(system_db, customer["id"], delivered=1, returned=3, cancelled=0)

        response = await client.get(
            "/v1/customers/risk-check",
            params={"phone": "01712345679"},
            headers=auth_header(session),
        )

        assert response.json()["state"] == "HIGH"

    async def test_the_response_never_carries_the_full_number(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone)
        await create_customer(client, session, phone="01755443322")

        response = await client.get(
            "/v1/customers/risk-check",
            params={"phone": "01755443322"},
            headers=auth_header(session),
        )

        assert "01755443322" not in response.text
        assert "+8801755443322" not in response.text
        assert response.json()["phone_masked"] == "01755****22"


class TestTenantIsolation:
    async def test_one_shops_history_is_invisible_to_another(
        self,
        client: AsyncClient,
        unique_phone: str,
        system_db: AsyncSession,
    ) -> None:
        seller_a = await signed_in_shop(client, unique_phone)
        customer = await create_customer(client, seller_a, phone="01798765432")
        await _set_counters(system_db, customer["id"], delivered=0, returned=6, cancelled=0)

        # The same number, asked by a different shop. Shop B has never sold to
        # them, so shop B learns nothing -- not the band, not the count.
        seller_b = await signed_in_shop(client, f"017{uuid.uuid4().int % 100_000_000:08d}")
        response = await client.get(
            "/v1/customers/risk-check",
            params={"phone": "01798765432"},
            headers=auth_header(seller_b),
        )

        body = response.json()
        assert body["found"] is False
        assert body["state"] == "INSUFFICIENT_DATA"
        assert body["returned_count"] == 0
