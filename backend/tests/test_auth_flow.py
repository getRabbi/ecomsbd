"""End-to-end authentication over HTTP.

Master spec sections 4, 47, 55 and 89. These run against the real ASGI app, so
middleware, dependencies, the tenancy guard and the error contract are all in
the path.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.errors import ErrorCode


async def request_code(client: AsyncClient, phone: str) -> dict[str, Any]:
    response = await client.post("/v1/auth/otp/request", json={"phone": phone})
    assert response.status_code == 200, response.text
    return response.json()


async def sign_in(client: AsyncClient, phone: str) -> dict[str, Any]:
    challenge = await request_code(client, phone)
    response = await client.post(
        "/v1/auth/otp/verify",
        json={
            "challenge_id": challenge["challenge_id"],
            "code": challenge["debug_code"],
            "device": {"install_id": f"install-{phone}", "platform": "ANDROID"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def auth_header(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access_token']}"}


class TestOtpRequest:
    async def test_returns_a_masked_number_never_the_full_one(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        body = await request_code(client, unique_phone)
        assert body["masked_phone"].count("*") == 4
        assert unique_phone not in body["masked_phone"]
        assert body["expires_in_seconds"] == 300

    async def test_bangla_digits_are_accepted(self, client: AsyncClient) -> None:
        response = await client.post("/v1/auth/otp/request", json={"phone": "০১৭৯৯৮৮৭৭৬৬"})
        assert response.status_code == 200
        assert response.json()["masked_phone"] == "01799****66"

    async def test_invalid_number_returns_a_typed_error(self, client: AsyncClient) -> None:
        response = await client.post("/v1/auth/otp/request", json={"phone": "01012345678"})
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == ErrorCode.INVALID_PHONE_NUMBER
        assert body["message_bn"]
        assert body["reference_id"]
        assert body["retryable"] is False

    async def test_debug_code_is_only_present_because_this_is_a_test_environment(
        self, client: AsyncClient, unique_phone: str, settings: Any
    ) -> None:
        body = await request_code(client, unique_phone)
        assert settings.expose_otp_debug_code is True
        assert body["debug_code"] is not None
        assert len(body["debug_code"]) == 6

    async def test_rate_limited_after_the_hourly_allowance(
        self, client: AsyncClient, unique_phone: str, settings: Any
    ) -> None:
        for _ in range(settings.otp_max_requests_per_phone_hour):
            await request_code(client, unique_phone)
        response = await client.post("/v1/auth/otp/request", json={"phone": unique_phone})
        assert response.status_code == 429
        assert response.json()["code"] == ErrorCode.OTP_RATE_LIMITED
        assert "retry-after" in response.headers


class TestOtpVerify:
    async def test_successful_sign_in_creates_a_new_user(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await sign_in(client, unique_phone)
        assert session["is_new_user"] is True
        assert session["tenant_id"] is None
        assert session["needs_onboarding"] is True
        assert session["access_token"] and session["refresh_token"]

    async def test_returning_user_is_recognised(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        await sign_in(client, unique_phone)
        second = await sign_in(client, unique_phone)
        assert second["is_new_user"] is False

    async def test_wrong_code_is_rejected_and_reports_remaining_attempts(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        challenge = await request_code(client, unique_phone)
        response = await client.post(
            "/v1/auth/otp/verify",
            json={"challenge_id": challenge["challenge_id"], "code": "000000"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == ErrorCode.OTP_INVALID
        assert body["details"]["attempts_remaining"] == 4

    async def test_attempts_are_capped(
        self, client: AsyncClient, unique_phone: str, settings: Any
    ) -> None:
        challenge = await request_code(client, unique_phone)
        for _ in range(settings.otp_max_attempts):
            await client.post(
                "/v1/auth/otp/verify",
                json={"challenge_id": challenge["challenge_id"], "code": "000000"},
            )
        # Even the correct code is refused once the challenge is burnt.
        response = await client.post(
            "/v1/auth/otp/verify",
            json={"challenge_id": challenge["challenge_id"], "code": challenge["debug_code"]},
        )
        assert response.status_code == 429
        assert response.json()["code"] == ErrorCode.OTP_MAX_ATTEMPTS

    async def test_a_code_cannot_be_used_twice(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        challenge = await request_code(client, unique_phone)
        payload = {
            "challenge_id": challenge["challenge_id"],
            "code": challenge["debug_code"],
        }
        assert (await client.post("/v1/auth/otp/verify", json=payload)).status_code == 200
        replay = await client.post("/v1/auth/otp/verify", json=payload)
        assert replay.status_code == 400
        assert replay.json()["code"] == ErrorCode.OTP_INVALID

    async def test_unknown_challenge_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/v1/auth/otp/verify",
            json={
                "challenge_id": "00000000-0000-4000-8000-000000000000",
                "code": "123456",
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == ErrorCode.OTP_INVALID


class TestSessionLifecycle:
    async def test_me_requires_a_token(self, client: AsyncClient) -> None:
        response = await client.get("/v1/me")
        assert response.status_code == 401
        assert response.json()["code"] == ErrorCode.UNAUTHENTICATED

    async def test_me_returns_the_signed_in_user(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        session = await sign_in(client, unique_phone)
        response = await client.get("/v1/me", headers=auth_header(session))
        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == session["user_id"]
        # The full number is never returned to the client.
        assert unique_phone not in response.text
        assert body["masked_phone"].endswith(unique_phone[-2:])

    async def test_refresh_rotates_the_token(self, client: AsyncClient, unique_phone: str) -> None:
        session = await sign_in(client, unique_phone)
        response = await client.post(
            "/v1/auth/refresh", json={"refresh_token": session["refresh_token"]}
        )
        assert response.status_code == 200
        rotated = response.json()
        assert rotated["refresh_token"] != session["refresh_token"]
        assert rotated["session_id"] == session["session_id"]

    async def test_reusing_a_rotated_token_revokes_the_session(
        self, client: AsyncClient, unique_phone: str
    ) -> None:
        # A refresh token is single-use. A second presentation means it was
        # captured, so the session dies rather than issuing more credentials.
        session = await sign_in(client, unique_phone)
        first = await client.post(
            "/v1/auth/refresh", json={"refresh_token": session["refresh_token"]}
        )
        assert first.status_code == 200

        replay = await client.post(
            "/v1/auth/refresh", json={"refresh_token": session["refresh_token"]}
        )
        assert replay.status_code == 401
        assert replay.json()["code"] == ErrorCode.SESSION_REVOKED

        # The replacement token issued a moment ago is dead too.
        after = await client.post(
            "/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}
        )
        assert after.status_code == 401

        # And the access token stops working immediately.
        me = await client.get("/v1/me", headers=auth_header(session))
        assert me.status_code == 401

    async def test_logout_revokes_the_session(self, client: AsyncClient, unique_phone: str) -> None:
        session = await sign_in(client, unique_phone)
        logout = await client.post(
            "/v1/auth/logout", json={"all_devices": False}, headers=auth_header(session)
        )
        assert logout.status_code == 204

        assert (await client.get("/v1/me", headers=auth_header(session))).status_code == 401
        refresh = await client.post(
            "/v1/auth/refresh", json={"refresh_token": session["refresh_token"]}
        )
        assert refresh.status_code == 401

    async def test_garbage_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/v1/me", headers={"Authorization": "Bearer nonsense"})
        assert response.status_code == 401
        assert response.json()["code"] == ErrorCode.INVALID_TOKEN


class TestCorrelationHeaders:
    async def test_every_response_carries_a_trace_id(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")
        assert response.headers["x-trace-id"]
        assert response.headers["x-request-id"]

    async def test_an_incoming_trace_id_is_preserved(self, client: AsyncClient) -> None:
        response = await client.get("/health/live", headers={"x-trace-id": "abc123"})
        assert response.headers["x-trace-id"] == "abc123"


class TestHealth:
    async def test_live(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_ready_reports_the_applied_migration(self, client: AsyncClient) -> None:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["checks"]["database"]["migration"]

    @pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
    async def test_health_needs_no_authentication(self, client: AsyncClient, path: str) -> None:
        assert (await client.get(path)).status_code == 200
