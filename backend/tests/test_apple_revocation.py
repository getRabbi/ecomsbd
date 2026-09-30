"""Sign in with Apple token revocation on account deletion.

App Store Review Guideline 5.1.1(v). The Apple calls are exercised against a
mock transport; no test reaches Apple.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import account
from app.auth.apple_revocation import (
    APPLE_REVOKE_URL,
    APPLE_TOKEN_URL,
    AppleRevocationOutcome,
    apple_client_secret,
    revoke_apple_authorization,
)
from app.common.audit import AuditAction, AuditLog
from app.core.config import Settings
from tests.conftest_commerce import signed_in_shop
from tests.test_auth_flow import auth_header

_KEY = ec.generate_private_key(ec.SECP256R1())
_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()


@pytest.fixture
def apple_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "apple_team_id": "TEAM123456",
            "apple_key_id": "KEY1234567",
            "apple_client_id": "com.ecomsbd.app",
            "apple_private_key": SecretStr(_PEM),
        }
    )


def _apple(
    token_status: int = 200, revoke_status: int = 200, tokens: dict | None = None
) -> tuple[httpx.AsyncClient, list[tuple[str, dict[str, list[str]]]]]:
    calls: list[tuple[str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), parse_qs(request.content.decode())))
        if str(request.url) == APPLE_TOKEN_URL:
            body = (
                tokens
                if token_status == 200
                else {"error": "invalid_grant", "error_description": "expired code"}
            )
            return httpx.Response(
                token_status,
                json=body
                if body is not None
                else {"access_token": "a-token", "refresh_token": "r-token", "id_token": "x"},
            )
        return httpx.Response(revoke_status)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


class TestClientSecret:
    def test_is_an_es256_jwt_apple_accepts(self, apple_settings: Settings) -> None:
        now = datetime(2026, 1, 15, 12, tzinfo=UTC)
        secret = apple_client_secret(apple_settings, now=now)
        assert secret is not None
        assert jwt.get_unverified_header(secret) == {
            "alg": "ES256",
            "kid": "KEY1234567",
            "typ": "JWT",
        }
        claims = jwt.decode(
            secret,
            _KEY.public_key(),
            algorithms=["ES256"],
            audience="https://appleid.apple.com",
            options={"verify_exp": False},
        )
        assert claims["iss"] == "TEAM123456"
        assert claims["sub"] == "com.ecomsbd.app"
        assert claims["exp"] - claims["iat"] == 300

    def test_accepts_a_single_line_pem(self, apple_settings: Settings) -> None:
        flattened = apple_settings.model_copy(
            update={"apple_private_key": SecretStr(_PEM.replace("\n", "\\n"))}
        )
        assert apple_client_secret(flattened) is not None

    def test_is_none_without_a_key(self, settings: Settings) -> None:
        assert apple_client_secret(settings.model_copy(update={"apple_private_key": None})) is None


class TestRevocation:
    async def test_exchanges_the_code_then_revokes_the_refresh_token(
        self, apple_settings: Settings
    ) -> None:
        client, calls = _apple()
        outcome = await revoke_apple_authorization("the-code", apple_settings, client=client)

        assert outcome is AppleRevocationOutcome.REVOKED
        assert [url for url, _ in calls] == [APPLE_TOKEN_URL, APPLE_REVOKE_URL]
        exchange, revoke = calls[0][1], calls[1][1]
        assert exchange["grant_type"] == ["authorization_code"]
        assert exchange["code"] == ["the-code"]
        assert exchange["client_id"] == ["com.ecomsbd.app"]
        assert revoke["token"] == ["r-token"]
        assert revoke["token_type_hint"] == ["refresh_token"]
        assert revoke["client_secret"] == exchange["client_secret"]

    async def test_falls_back_to_the_access_token(self, apple_settings: Settings) -> None:
        client, calls = _apple(tokens={"access_token": "a-token", "id_token": "x"})
        outcome = await revoke_apple_authorization("the-code", apple_settings, client=client)
        assert outcome is AppleRevocationOutcome.REVOKED
        assert calls[1][1]["token"] == ["a-token"]
        assert calls[1][1]["token_type_hint"] == ["access_token"]

    async def test_a_refused_code_revokes_nothing(self, apple_settings: Settings) -> None:
        client, calls = _apple(token_status=400)
        outcome = await revoke_apple_authorization("stale", apple_settings, client=client)
        assert outcome is AppleRevocationOutcome.FAILED
        assert [url for url, _ in calls] == [APPLE_TOKEN_URL]

    async def test_a_refused_revocation_is_a_failure(self, apple_settings: Settings) -> None:
        client, _ = _apple(revoke_status=400)
        outcome = await revoke_apple_authorization("the-code", apple_settings, client=client)
        assert outcome is AppleRevocationOutcome.FAILED

    async def test_unreachable_apple_is_a_failure(self, apple_settings: Settings) -> None:
        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(unreachable))
        outcome = await revoke_apple_authorization("the-code", apple_settings, client=client)
        assert outcome is AppleRevocationOutcome.FAILED

    async def test_without_a_key_nothing_is_sent(self, settings: Settings) -> None:
        client, calls = _apple()
        unconfigured = settings.model_copy(update={"apple_private_key": None})
        outcome = await revoke_apple_authorization("the-code", unconfigured, client=client)
        assert outcome is AppleRevocationOutcome.NOT_CONFIGURED
        assert calls == []

    async def test_a_malformed_key_is_a_failure(self, apple_settings: Settings) -> None:
        client, calls = _apple()
        broken = apple_settings.model_copy(update={"apple_private_key": SecretStr("not a key")})
        outcome = await revoke_apple_authorization("the-code", broken, client=client)
        assert outcome is AppleRevocationOutcome.FAILED
        assert calls == []


class TestDeletionRevokesApple:
    @pytest.fixture
    def revocations(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        seen: list[str] = []

        async def fake(code: str, settings: Settings) -> AppleRevocationOutcome:
            seen.append(code)
            return (
                AppleRevocationOutcome.FAILED
                if code == "apple-down"
                else AppleRevocationOutcome.REVOKED
            )

        monkeypatch.setattr(account, "revoke_apple_authorization", fake)
        return seen

    async def _audited(self, system_db: AsyncSession, user_id: str) -> list[dict]:
        rows = (
            (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.APPLE_AUTHORIZATION_REVOKED),
                        AuditLog.entity_id == user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [row.context for row in rows]

    async def test_an_apple_seller_is_revoked_and_scheduled(
        self,
        client: AsyncClient,
        unique_phone: str,
        system_db: AsyncSession,
        revocations: list[str],
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Apple Shop")
        response = await client.post(
            "/v1/account/delete",
            json={"confirm": "CLOSE", "apple_authorization_code": "fresh-code"},
            headers=auth_header(session),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "SCHEDULED"
        assert revocations == ["fresh-code"]
        assert await self._audited(system_db, session["user_id"]) == [{"outcome": "revoked"}]

    async def test_apple_failing_does_not_block_deletion(
        self,
        client: AsyncClient,
        unique_phone: str,
        system_db: AsyncSession,
        revocations: list[str],
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Apple Shop")
        response = await client.post(
            "/v1/account/delete",
            json={"confirm": "CLOSE", "apple_authorization_code": "apple-down"},
            headers=auth_header(session),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "SCHEDULED"
        assert await self._audited(system_db, session["user_id"]) == [{"outcome": "failed"}]

    async def test_other_sellers_touch_no_apple_endpoint(
        self,
        client: AsyncClient,
        unique_phone: str,
        system_db: AsyncSession,
        revocations: list[str],
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Email Shop")
        response = await client.post(
            "/v1/account/delete", json={"confirm": "CLOSE"}, headers=auth_header(session)
        )
        assert response.status_code == 200, response.text
        assert revocations == []
        assert await self._audited(system_db, session["user_id"]) == []

    async def test_a_refused_deletion_revokes_nothing(
        self, client: AsyncClient, unique_phone: str, revocations: list[str]
    ) -> None:
        session = await signed_in_shop(client, unique_phone, shop_name="Apple Shop")
        await client.post(
            "/v1/account/delete", json={"confirm": "CLOSE"}, headers=auth_header(session)
        )
        again = await client.post(
            "/v1/account/delete",
            json={"confirm": "CLOSE", "apple_authorization_code": "fresh-code"},
            headers=auth_header(session),
        )
        assert again.status_code == 409
        assert revocations == []
