"""``python -m app.admin.issue_token``: per-operator admin tokens.

What must hold: the printed token authenticates through the real
``X-Admin-Token`` path with the role asked for; only its keyed hash is stored;
issuing is audited without the token; a second live credential under one label
is refused; and stdout carries the token and nothing else.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.admin import issue_token as cli
from app.admin.models import PlatformAdmin, PlatformAdminRole
from app.common.audit import AuditAction, AuditLog
from tests.test_admin import admin_client, admin_header  # noqa: F401 - fixture import

#: Every test runs with the admin console configured, as in ``test_admin``.
pytestmark = pytest.mark.usefixtures("admin_client")


def _settings() -> Any:
    from app.core.config import get_settings

    return get_settings()


async def _row(admin_id: uuid.UUID) -> PlatformAdmin:
    from app.db.session import system_session

    async with system_session("test: read platform admin") as session:
        row = await session.get(PlatformAdmin, admin_id)
        assert row is not None
        return row


def _label() -> str:
    return f"operator-{uuid.uuid4().hex[:8]}"


class TestIssuedTokenWorks:
    async def test_an_ops_token_authenticates_as_that_operator(self, client: AsyncClient) -> None:
        label = _label()
        issued = await cli.issue_admin_token(label=label, role="OPS", settings=_settings())

        response = await client.get("/v1/admin/me", headers=admin_header(issued.token))
        assert response.status_code == 200
        body = response.json()
        assert body["role"] == "OPS"
        assert body["is_bootstrap"] is False
        assert body["label"] == label
        assert "admin.repair_run" in body["permissions"]
        assert "admin.pii_reveal" not in body["permissions"]

    async def test_a_superadmin_token_carries_every_permission(self, client: AsyncClient) -> None:
        issued = await cli.issue_admin_token(
            label=_label(), role=PlatformAdminRole.SUPERADMIN, settings=_settings()
        )
        body = (await client.get("/v1/admin/me", headers=admin_header(issued.token))).json()
        assert body["role"] == "SUPERADMIN"
        assert "admin.pii_reveal" in body["permissions"]

    async def test_the_token_is_long_and_unique(self) -> None:
        first = await cli.issue_admin_token(label=_label(), role="OPS", settings=_settings())
        second = await cli.issue_admin_token(label=_label(), role="OPS", settings=_settings())
        assert len(first.token) >= 32
        assert first.token != second.token


class TestOnlyTheHashIsKept:
    async def test_the_row_holds_the_keyed_hash_not_the_token(self) -> None:
        from app.core.security import SecretHasher

        issued = await cli.issue_admin_token(label=_label(), role="OPS", settings=_settings())
        row = await _row(issued.admin_id)

        assert row.token_hash == SecretHasher(_settings()).token_hash(issued.token)
        stored = " ".join(str(value) for value in vars(row).values())
        assert issued.token not in stored
        assert row.is_active is True
        assert row.expires_at is None

    async def test_issuing_is_audited_without_the_token(self) -> None:
        from app.db.session import system_session

        label = _label()
        issued = await cli.issue_admin_token(label=label, role="OPS", settings=_settings())

        async with system_session("test: read audit") as session:
            entry = (
                await session.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.ADMIN_TOKEN_ISSUED),
                        AuditLog.entity_id == str(issued.admin_id),
                    )
                )
            ).scalar_one()
        assert entry.context["label"] == label
        assert entry.context["role"] == "OPS"
        assert issued.token not in str(entry.context)
        assert issued.token not in str(entry.reason)

    async def test_an_expiry_is_written_when_asked_for(self) -> None:
        issued = await cli.issue_admin_token(
            label=_label(), role="OPS", settings=_settings(), expires_in_days=30
        )
        row = await _row(issued.admin_id)
        assert row.expires_at is not None
        assert row.is_usable()


class TestRefusals:
    async def test_a_second_live_token_under_one_label_is_refused(self) -> None:
        label = _label()
        await cli.issue_admin_token(label=label, role="OPS", settings=_settings())
        with pytest.raises(ValueError, match="already exists"):
            await cli.issue_admin_token(label=label, role="SUPERADMIN", settings=_settings())

    async def test_a_revoked_label_can_be_issued_again(self, client: AsyncClient) -> None:
        from app.core.clock import utc_now
        from app.db.session import system_session

        label = _label()
        old = await cli.issue_admin_token(label=label, role="OPS", settings=_settings())
        async with system_session("test: revoke admin") as session:
            row = await session.get(PlatformAdmin, old.admin_id)
            assert row is not None
            row.revoked_at = utc_now()
            row.revoked_reason = "rotation"

        new = await cli.issue_admin_token(label=label, role="OPS", settings=_settings())
        assert (
            await client.get("/v1/admin/me", headers=admin_header(old.token))
        ).status_code == 401
        assert (
            await client.get("/v1/admin/me", headers=admin_header(new.token))
        ).status_code == 200

    @pytest.mark.parametrize("label", ["", "   ", "bootstrap:0", "x" * 81])
    async def test_bad_labels_are_refused(self, label: str) -> None:
        with pytest.raises(ValueError):
            await cli.issue_admin_token(label=label, role="OPS", settings=_settings())

    async def test_an_unknown_role_is_refused(self) -> None:
        with pytest.raises(ValueError):
            await cli.issue_admin_token(label=_label(), role="ROOT", settings=_settings())

    async def test_a_non_positive_expiry_is_refused(self) -> None:
        with pytest.raises(ValueError):
            await cli.issue_admin_token(
                label=_label(), role="OPS", settings=_settings(), expires_in_days=0
            )


class TestCommandLine:
    def test_the_role_is_case_insensitive_and_limited(self) -> None:
        parser = cli.build_parser()
        assert parser.parse_args(["--label", "a", "--role", "ops"]).role == "OPS"
        assert parser.parse_args(["--label", "a", "--role", "SUPERADMIN"]).role == "SUPERADMIN"
        with pytest.raises(SystemExit):
            parser.parse_args(["--label", "a", "--role", "ROOT"])
        with pytest.raises(SystemExit):
            parser.parse_args(["--role", "OPS"])

    def test_stdout_carries_only_the_token(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        token = "INSECURE_DEV_cli_token_for_tests_only_abcdefghijklmnopqrstuv"
        seen: dict[str, Any] = {}

        async def fake_run(args: Any) -> cli.IssuedToken:
            seen.update(label=args.label, role=args.role, days=args.expires_in_days)
            return cli.IssuedToken(
                admin_id=uuid.uuid4(), label=args.label, role=PlatformAdminRole.OPS, token=token
            )

        monkeypatch.setattr(cli, "_run", fake_run)
        assert cli.main(["--label", "rabbi", "--role", "ops"]) == 0

        out, err = capsys.readouterr()
        assert out == token + "\n"
        assert token not in err
        assert "shown once" in err
        assert seen == {"label": "rabbi", "role": "OPS", "days": None}

    def test_a_refusal_exits_2_and_prints_no_token(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def refuse(args: Any) -> cli.IssuedToken:
            raise ValueError("An active admin token labelled 'rabbi' already exists")

        monkeypatch.setattr(cli, "_run", refuse)
        assert cli.main(["--label", "rabbi", "--role", "OPS"]) == 2

        out, err = capsys.readouterr()
        assert out == ""
        assert "already exists" in err
