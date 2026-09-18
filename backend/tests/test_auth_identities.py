"""Email/password, Google and Apple sign-in.

These run against the real ASGI app, so middleware, dependencies, the tenancy
guard and the error contract are all in the path — the same arrangement as
``test_auth_flow.py``, for the same reason.

The Google and Apple tokens are real JWTs signed by a key this module
generates, served through a seeded JWKS cache. Nothing reaches the network, and
nothing is stubbed at the level that matters: the verifier does the same
signature, issuer, audience and expiry work it will do in production, which is
what makes the wrong-audience and expired-token tests evidence rather than
decoration.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identities import AuthIdentity, AuthProvider, AuthToken, AuthTokenPurpose
from app.auth.models import AuthSession
from app.auth.oidc import (
    APPLE_ISSUER,
    APPLE_JWKS_URI,
    GOOGLE_JWKS_URI,
    JwksCache,
    OidcTokenVerifier,
)
from app.auth.passwords import PasswordHasher, validate_password
from app.core.clock import utc_now
from app.core.errors import AuthenticationError, ErrorCode, ValidationError
from app.users.models import User, UserStatus

GOOGLE_AUDIENCE = "111111111111-ecomsbd-android.apps.googleusercontent.com"
APPLE_AUDIENCE = "com.ecomsbd.test"
PASSWORD = "correct-horse-battery"

# --------------------------------------------------------------------------- #
# Signing fixtures
# --------------------------------------------------------------------------- #

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-key-1"


def _jwks() -> dict[str, Any]:
    numbers = _KEY.public_key().public_numbers()

    def b64(value: int) -> str:
        import base64

        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": _KID,
                "use": "sig",
                "alg": "RS256",
                "n": b64(numbers.n),
                "e": b64(numbers.e),
            }
        ]
    }


def make_id_token(
    *,
    issuer: str,
    audience: str,
    subject: str,
    email: str | None = None,
    email_verified: bool | None = True,
    expires_in: int = 600,
    name: str | None = None,
    hosted_domain: str | None = None,
) -> str:
    now = utc_now()
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    if email is not None:
        claims["email"] = email
    if email_verified is not None:
        claims["email_verified"] = email_verified
    if name is not None:
        claims["name"] = name
    if hosted_domain is not None:
        claims["hd"] = hosted_domain
    return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": _KID})


def google_token(**kwargs: Any) -> str:
    kwargs.setdefault("issuer", "https://accounts.google.com")
    kwargs.setdefault("audience", GOOGLE_AUDIENCE)
    # These example.com fixtures represent Google Workspace identities.
    kwargs.setdefault("hosted_domain", "example.com")
    return make_id_token(**kwargs)


def apple_token(**kwargs: Any) -> str:
    kwargs.setdefault("issuer", APPLE_ISSUER)
    kwargs.setdefault("audience", APPLE_AUDIENCE)
    return make_id_token(**kwargs)


# --------------------------------------------------------------------------- #
# App fixture
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def auth_client(settings: Any) -> AsyncIterator[AsyncClient]:
    """An app with all three production sign-in methods enabled.

    The verifiers are the real ones, with their JWKS caches seeded instead of
    fetched. `get_app_settings` resolves the process-wide singleton, so the
    settings override has to go through `dependency_overrides` — handing a
    different Settings to `create_app` alone would not reach the dependency
    graph.
    """
    from app.api.deps import (
        get_app_settings,
        get_apple_verifier_dep,
        get_google_verifier_dep,
        reset_singletons,
    )
    from app.main import create_app

    configured = settings.model_copy(
        update={
            "email_password_auth_enabled": True,
            "phone_otp_login_enabled": False,
            "google_auth_enabled": True,
            "apple_auth_enabled": True,
            "google_client_id_android": GOOGLE_AUDIENCE,
            "apple_client_id": APPLE_AUDIENCE,
            "apple_team_id": "ABCDE12345",
            "apple_key_id": "FGHIJ67890",
        }
    )
    reset_singletons()

    cache = JwksCache()
    cache.seed(GOOGLE_JWKS_URI, _jwks())
    cache.seed(APPLE_JWKS_URI, _jwks())
    google = OidcTokenVerifier(
        provider=AuthProvider.GOOGLE,
        issuers=("https://accounts.google.com", "accounts.google.com"),
        jwks_uri=GOOGLE_JWKS_URI,
        audiences=(GOOGLE_AUDIENCE,),
        jwks=cache,
    )
    apple = OidcTokenVerifier(
        provider=AuthProvider.APPLE,
        issuers=(APPLE_ISSUER,),
        jwks_uri=APPLE_JWKS_URI,
        audiences=(APPLE_AUDIENCE,),
        jwks=cache,
    )

    app = create_app(configured)
    app.dependency_overrides[get_app_settings] = lambda: configured
    app.dependency_overrides[get_google_verifier_dep] = lambda: google
    app.dependency_overrides[get_apple_verifier_dep] = lambda: apple
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
        app.router.lifespan_context(app),
    ):
        yield client
    app.dependency_overrides.clear()
    reset_singletons()


@pytest.fixture
def unique_email() -> Callable[[str], str]:
    def make(prefix: str = "seller") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"

    return make


async def register(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "device": {"install_id": "i-1"}},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --------------------------------------------------------------------------- #
# Password policy and hashing
# --------------------------------------------------------------------------- #


class TestPasswordHashing:
    def test_a_digest_never_contains_the_password(self) -> None:
        encoded = PasswordHasher().hash(PASSWORD)
        assert PASSWORD not in encoded
        assert encoded.startswith("$argon2id$")

    def test_the_same_password_hashes_differently_each_time(self) -> None:
        """A shared salt would make the table sortable by password."""
        hasher = PasswordHasher()
        assert hasher.hash(PASSWORD) != hasher.hash(PASSWORD)

    def test_verify_round_trips_and_rejects(self) -> None:
        hasher = PasswordHasher()
        encoded = hasher.hash(PASSWORD)
        assert hasher.verify(PASSWORD, encoded)
        assert not hasher.verify(PASSWORD + "x", encoded)

    def test_a_missing_digest_is_false_not_an_error(self) -> None:
        """A Google-only account must fail a password login, not crash."""
        assert PasswordHasher().verify(PASSWORD, None) is False
        assert PasswordHasher().verify(PASSWORD, "not-a-phc-string") is False

    def test_a_weaker_stored_digest_is_flagged_for_rehash(self) -> None:
        weak = PasswordHasher(memory_cost=8 * 1024, iterations=1).hash(PASSWORD)
        strong = PasswordHasher()
        assert strong.verify(PASSWORD, weak)
        assert strong.needs_rehash(weak)
        assert not strong.needs_rehash(strong.hash(PASSWORD))

    @pytest.mark.parametrize("bad", ["short", "password123", "1234567890"])
    def test_obvious_and_short_passwords_are_refused(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            validate_password(bad)


# --------------------------------------------------------------------------- #
# Email + password
# --------------------------------------------------------------------------- #


class TestRegistration:
    async def test_registering_issues_a_session_and_needs_onboarding(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        body = await register(auth_client, unique_email("new"))
        session = body["session"]
        assert session["access_token"]
        assert session["refresh_token"]
        assert session["is_new_user"] is True
        # No shop yet, so the client routes to onboarding — the same answer
        # every other sign-in method gives.
        assert session["needs_onboarding"] is True
        assert session["tenant_id"] is None
        # The default transport sends nothing, even though a local debug token exists.
        assert body["email_verification_sent"] is False

    async def test_a_duplicate_email_is_refused(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("dup")
        await register(auth_client, email)
        again = await auth_client.post(
            "/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        assert again.status_code == 409
        assert again.json()["code"] == ErrorCode.EMAIL_ALREADY_REGISTERED

    async def test_the_email_is_matched_case_insensitively(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("case")
        await register(auth_client, email)
        again = await auth_client.post(
            "/v1/auth/register", json={"email": email.upper(), "password": PASSWORD}
        )
        assert again.status_code == 409

    async def test_no_response_ever_carries_the_password_or_its_hash(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("leak")
        created = await auth_client.post(
            "/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        signed_in = await auth_client.post(
            "/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        for response in (created, signed_in):
            assert PASSWORD not in response.text
            assert "argon2" not in response.text
            assert "password_hash" not in response.text

    async def test_a_weak_password_is_refused_before_an_account_exists(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("weak")
        response = await auth_client.post(
            "/v1/auth/register", json={"email": email, "password": "short"}
        )
        # 422 is this API's VALIDATION_ERROR status, and the body says which
        # field and why, so the form can point at it.
        assert response.status_code == 422
        assert response.json()["details"]["field"] == "password"
        # And the address is still free.
        assert (await register(auth_client, email))["session"]["is_new_user"] is True


class TestPasswordLogin:
    async def test_a_correct_password_signs_in(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("ok")
        created = await register(auth_client, email)
        response = await auth_client.post(
            "/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == created["session"]["user_id"]
        assert response.json()["is_new_user"] is False

    async def test_a_wrong_password_is_refused(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("wrong")
        await register(auth_client, email)
        response = await auth_client.post(
            "/v1/auth/login", json={"email": email, "password": "not-the-password"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == ErrorCode.INVALID_CREDENTIALS

    async def test_an_unknown_address_is_refused_identically(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        """Enumeration protection: the two refusals must be indistinguishable."""
        email = unique_email("known")
        await register(auth_client, email)
        wrong_password = await auth_client.post(
            "/v1/auth/login", json={"email": email, "password": "not-the-password"}
        )
        no_account = await auth_client.post(
            "/v1/auth/login",
            json={"email": unique_email("ghost"), "password": PASSWORD},
        )
        assert wrong_password.status_code == no_account.status_code == 401
        assert wrong_password.json()["code"] == no_account.json()["code"]
        assert wrong_password.json()["message_en"] == no_account.json()["message_en"]


# --------------------------------------------------------------------------- #
# Email verification and password reset
# --------------------------------------------------------------------------- #


class TestEmailVerification:
    async def test_a_verification_token_marks_the_identity_verified(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        email = unique_email("verify")
        body = await register(auth_client, email)
        token = body["verification_token"]
        assert token, "the test environment has no email provider, so the token is returned"

        response = await auth_client.post("/v1/auth/email/verify", json={"token": token})
        assert response.status_code == 200

        identity = (
            await system_db.execute(
                sa.select(AuthIdentity).where(AuthIdentity.normalized_email == email)
            )
        ).scalar_one()
        assert identity.email_verified is True

    async def test_a_verification_token_cannot_be_replayed(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        token = (await register(auth_client, unique_email("replay")))["verification_token"]
        assert (
            await auth_client.post("/v1/auth/email/verify", json={"token": token})
        ).status_code == 200
        again = await auth_client.post("/v1/auth/email/verify", json={"token": token})
        assert again.status_code == 401
        assert again.json()["code"] == ErrorCode.INVALID_TOKEN

    async def test_resending_says_nothing_about_whether_the_account_exists(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        known = unique_email("resend")
        await register(auth_client, known)
        for address in (known, unique_email("ghost")):
            response = await auth_client.post("/v1/auth/email/resend", json={"email": address})
            assert response.status_code == 200
            assert response.json()["acknowledged"] is True


class TestPasswordReset:
    async def test_forgot_password_acknowledges_every_address_identically(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        known = unique_email("forgot")
        await register(auth_client, known)
        real = await auth_client.post("/v1/auth/password/forgot", json={"email": known})
        ghost = await auth_client.post(
            "/v1/auth/password/forgot", json={"email": unique_email("ghost")}
        )
        assert real.status_code == ghost.status_code == 200
        assert real.json()["acknowledged"] == ghost.json()["acknowledged"] is True

    async def test_a_reset_sets_the_new_password_and_revokes_old_sessions(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        email = unique_email("reset")
        created = await register(auth_client, email)
        user_id = uuid.UUID(created["session"]["user_id"])

        forgot = await auth_client.post("/v1/auth/password/forgot", json={"email": email})
        token = forgot.json()["debug_token"]
        assert token

        new_password = "a-brand-new-secret-99"
        done = await auth_client.post(
            "/v1/auth/password/reset", json={"token": token, "new_password": new_password}
        )
        assert done.status_code == 200

        # The session that existed before the reset is gone. A reset is what
        # someone does when they think another person is in their account.
        live = (
            await system_db.execute(
                sa.select(sa.func.count())
                .select_from(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            )
        ).scalar_one()
        assert live == 0

        assert (
            await auth_client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        ).status_code == 401
        assert (
            await auth_client.post(
                "/v1/auth/login", json={"email": email, "password": new_password}
            )
        ).status_code == 200

    async def test_a_reset_token_cannot_be_replayed(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("replay-reset")
        await register(auth_client, email)
        token = (await auth_client.post("/v1/auth/password/forgot", json={"email": email})).json()[
            "debug_token"
        ]

        first = await auth_client.post(
            "/v1/auth/password/reset", json={"token": token, "new_password": "first-new-pass-1"}
        )
        assert first.status_code == 200
        second = await auth_client.post(
            "/v1/auth/password/reset", json={"token": token, "new_password": "second-new-pass-2"}
        )
        assert second.status_code == 401
        # And the replay did not change the password.
        assert (
            await auth_client.post(
                "/v1/auth/login", json={"email": email, "password": "first-new-pass-1"}
            )
        ).status_code == 200

    async def test_an_expired_reset_token_is_refused(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        email = unique_email("expired")
        await register(auth_client, email)
        token = (await auth_client.post("/v1/auth/password/forgot", json={"email": email})).json()[
            "debug_token"
        ]

        await system_db.execute(
            sa.update(AuthToken)
            .where(AuthToken.purpose == AuthTokenPurpose.PASSWORD_RESET)
            .values(expires_at=utc_now() - timedelta(minutes=1))
        )
        await system_db.commit()

        response = await auth_client.post(
            "/v1/auth/password/reset", json={"token": token, "new_password": "another-pass-33"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == ErrorCode.TOKEN_EXPIRED

    async def test_issuing_a_new_reset_token_kills_the_previous_one(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        """Two live reset links double the window a stolen inbox is an account."""
        email = unique_email("supersede")
        await register(auth_client, email)
        first = (await auth_client.post("/v1/auth/password/forgot", json={"email": email})).json()[
            "debug_token"
        ]
        second = (await auth_client.post("/v1/auth/password/forgot", json={"email": email})).json()[
            "debug_token"
        ]
        assert first != second

        stale = await auth_client.post(
            "/v1/auth/password/reset", json={"token": first, "new_password": "stale-attempt-1"}
        )
        assert stale.status_code == 401
        fresh = await auth_client.post(
            "/v1/auth/password/reset", json={"token": second, "new_password": "fresh-attempt-2"}
        )
        assert fresh.status_code == 200


class TestPasswordChange:
    async def test_changing_requires_the_current_password(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("change")
        created = await register(auth_client, email)
        header = {"Authorization": f"Bearer {created['session']['access_token']}"}

        wrong = await auth_client.post(
            "/v1/auth/password/change",
            json={"current_password": "not-it", "new_password": "replacement-pass-1"},
            headers=header,
        )
        assert wrong.status_code == 401

        right = await auth_client.post(
            "/v1/auth/password/change",
            json={"current_password": PASSWORD, "new_password": "replacement-pass-1"},
            headers=header,
        )
        assert right.status_code == 200
        assert (
            await auth_client.post(
                "/v1/auth/login", json={"email": email, "password": "replacement-pass-1"}
            )
        ).status_code == 200


# --------------------------------------------------------------------------- #
# Google and Apple
# --------------------------------------------------------------------------- #


class TestProviderSignIn:
    async def test_a_valid_google_token_creates_an_account(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("google")
        response = await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject="g-1", email=email), "device": {}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_new_user"] is True
        assert body["needs_onboarding"] is True

    async def test_the_same_subject_returns_the_same_user(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("repeat")
        first = await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject="g-repeat", email=email)},
        )
        # Same `sub`, different address: the provider's subject is what
        # identifies the account, so this must not be a new user.
        second = await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject="g-repeat", email=unique_email("moved"))},
        )
        assert second.status_code == 200
        assert second.json()["user_id"] == first.json()["user_id"]
        assert second.json()["is_new_user"] is False

    async def test_a_token_for_another_application_is_refused(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        """The audience check. A valid Google token minted for someone else's app."""
        response = await auth_client.post(
            "/v1/auth/oauth/google",
            json={
                "id_token": google_token(
                    subject="g-other",
                    email=unique_email("other"),
                    audience="999-someone-elses-app.apps.googleusercontent.com",
                )
            },
        )
        assert response.status_code == 401
        assert response.json()["code"] == ErrorCode.INVALID_TOKEN

    async def test_an_expired_google_token_is_refused(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        response = await auth_client.post(
            "/v1/auth/oauth/google",
            json={
                "id_token": google_token(
                    subject="g-expired", email=unique_email("exp"), expires_in=-3600
                )
            },
        )
        assert response.status_code == 401

    async def test_a_token_from_the_wrong_issuer_is_refused(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        response = await auth_client.post(
            "/v1/auth/oauth/google",
            json={
                "id_token": make_id_token(
                    issuer="https://evil.example.com",
                    audience=GOOGLE_AUDIENCE,
                    subject="g-evil",
                    email=unique_email("evil"),
                )
            },
        )
        assert response.status_code == 401

    async def test_a_client_claim_of_success_is_not_a_token(self, auth_client: AsyncClient) -> None:
        response = await auth_client.post(
            "/v1/auth/oauth/google", json={"id_token": "google_login_success=true"}
        )
        assert response.status_code == 401

    async def test_an_apple_token_without_an_email_signs_in(self, auth_client: AsyncClient) -> None:
        """Apple sends a name and email only on the first authorization."""
        first = await auth_client.post(
            "/v1/auth/oauth/apple",
            json={
                "id_token": apple_token(
                    subject="a-1", email="first@example.com", name="First Seller"
                )
            },
        )
        assert first.status_code == 200, first.text

        returning = await auth_client.post(
            "/v1/auth/oauth/apple",
            json={"id_token": apple_token(subject="a-1", email=None, email_verified=None)},
        )
        assert returning.status_code == 200
        assert returning.json()["user_id"] == first.json()["user_id"]
        assert returning.json()["is_new_user"] is False

    async def test_an_apple_token_for_another_application_is_refused(
        self, auth_client: AsyncClient
    ) -> None:
        response = await auth_client.post(
            "/v1/auth/oauth/apple",
            json={"id_token": apple_token(subject="a-other", audience="com.someone.else")},
        )
        assert response.status_code == 401

    async def test_an_expired_apple_token_is_refused(self, auth_client: AsyncClient) -> None:
        response = await auth_client.post(
            "/v1/auth/oauth/apple",
            json={"id_token": apple_token(subject="a-expired", expires_in=-60)},
        )
        assert response.status_code == 401

    async def test_an_apple_relay_address_never_links_an_account(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        """A relay address identifies an Apple account, not a mailbox elsewhere."""
        relay = "abc123@privaterelay.appleid.com"
        password_email = unique_email("relay")
        created = await register(auth_client, password_email)
        await _mark_verified(system_db, password_email)

        response = await auth_client.post(
            "/v1/auth/oauth/apple",
            json={"id_token": apple_token(subject="a-relay", email=relay)},
        )
        assert response.status_code == 200
        assert response.json()["user_id"] != created["session"]["user_id"]


# --------------------------------------------------------------------------- #
# Account linking and takeover
# --------------------------------------------------------------------------- #


async def _mark_verified(system_db: AsyncSession, email: str) -> None:
    await system_db.execute(
        sa.update(AuthIdentity)
        .where(AuthIdentity.normalized_email == email)
        .values(email_verified=True)
    )
    await system_db.commit()


class TestAccountLinking:
    async def test_changed_unverified_email_cannot_inherit_verification(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        subject = f"g-change-{uuid.uuid4().hex}"
        original = await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject=subject, email=unique_email("old"))},
        )
        changed_email = unique_email("new-unverified")
        changed = await auth_client.post(
            "/v1/auth/oauth/google",
            json={
                "id_token": google_token(subject=subject, email=changed_email, email_verified=False)
            },
        )
        assert changed.json()["user_id"] == original.json()["user_id"]
        apple = await auth_client.post(
            "/v1/auth/oauth/apple",
            json={"id_token": apple_token(subject=uuid.uuid4().hex, email=changed_email)},
        )
        assert apple.status_code == 200
        assert apple.json()["user_id"] != original.json()["user_id"]

    async def test_google_third_party_mailbox_cannot_automatically_link(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        email = unique_email("third-party")
        created = await register(auth_client, email)
        await _mark_verified(system_db, email)
        response = await auth_client.post(
            "/v1/auth/oauth/google",
            json={
                "id_token": google_token(subject=uuid.uuid4().hex, email=email, hosted_domain=None)
            },
        )
        assert response.status_code == 200
        assert response.json()["user_id"] != created["session"]["user_id"]

    async def test_a_verified_email_links_google_to_the_existing_user(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        email = unique_email("link")
        created = await register(auth_client, email)
        await _mark_verified(system_db, email)

        response = await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject="g-link", email=email)},
        )
        assert response.status_code == 200
        assert response.json()["user_id"] == created["session"]["user_id"]
        assert response.json()["is_new_user"] is False

        identities = list(
            (
                await system_db.execute(
                    sa.select(AuthIdentity).where(
                        AuthIdentity.user_id == uuid.UUID(created["session"]["user_id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {row.provider for row in identities} == {
            AuthProvider.PASSWORD,
            AuthProvider.GOOGLE,
        }

    async def test_an_unverified_squatter_cannot_capture_a_google_account(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        """The pre-hijack attack, refused.

        An attacker registers the victim's address with a password and never
        verifies it. When the real owner arrives through Google, linking on the
        address alone would hand them — and their shop — to the attacker.
        """
        victim_email = unique_email("victim")
        squatter = await register(auth_client, victim_email)  # never verified

        response = await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject="g-victim", email=victim_email)},
        )
        assert response.status_code == 200
        assert response.json()["user_id"] != squatter["session"]["user_id"]
        assert response.json()["is_new_user"] is True

    async def test_an_unverified_provider_email_cannot_link(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        """The mirror case: Google itself says the address is not verified."""
        email = unique_email("unverified-side")
        created = await register(auth_client, email)
        await _mark_verified(system_db, email)

        response = await auth_client.post(
            "/v1/auth/oauth/google",
            json={
                "id_token": google_token(subject="g-unverified", email=email, email_verified=False)
            },
        )
        assert response.status_code == 200
        assert response.json()["user_id"] != created["session"]["user_id"]

    async def test_a_provider_subject_stays_bound_to_its_own_user(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        """A second Google account cannot ride in on a linked user's address."""
        email = unique_email("bound")
        created = await register(auth_client, email)
        await _mark_verified(system_db, email)
        await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject="g-bound", email=email)},
        )

        # A different `sub`, same verified address. The user already holds a
        # GOOGLE identity, so this must not attach a second one to them.
        intruder = await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject="g-intruder", email=email)},
        )
        assert intruder.status_code in (200, 409)
        if intruder.status_code == 200:
            assert intruder.json()["user_id"] != created["session"]["user_id"]

        google_rows = list(
            (
                await system_db.execute(
                    sa.select(AuthIdentity).where(
                        AuthIdentity.user_id == uuid.UUID(created["session"]["user_id"]),
                        AuthIdentity.provider == AuthProvider.GOOGLE,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(google_rows) == 1
        assert google_rows[0].provider_subject == "g-bound"

    async def test_two_established_users_are_never_merged(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        """Ambiguity is resolved by declining to guess, not by picking one.

        Two separate users each holding the same *proven* address should not
        happen, and the API will not produce it — an Apple sign-in on a verified
        address links to the existing user rather than making a second one. So
        the state is arranged directly here, which is the only way to reach the
        branch and the reason the branch exists: if it ever does happen, linking
        must not pick a winner.
        """
        shared = unique_email("shared")
        first = await register(auth_client, shared)
        await _mark_verified(system_db, shared)

        second_user = User(status=UserStatus.ACTIVE, display_name="Second")
        system_db.add(second_user)
        await system_db.flush()
        system_db.add(
            AuthIdentity(
                user_id=second_user.id,
                provider=AuthProvider.APPLE,
                provider_subject="a-shared-duplicate",
                normalized_email=shared,
                email_verified=True,
            )
        )
        await system_db.commit()

        google = await auth_client.post(
            "/v1/auth/oauth/google",
            json={"id_token": google_token(subject="g-shared", email=shared)},
        )
        assert google.status_code == 200
        assert google.json()["is_new_user"] is True
        assert google.json()["user_id"] != first["session"]["user_id"]
        assert google.json()["user_id"] != str(second_user.id)


# --------------------------------------------------------------------------- #
# The session system is shared
# --------------------------------------------------------------------------- #


class TestSharedSessionSystem:
    async def test_a_password_session_refreshes_and_detects_reuse(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        """Phase A's rotation and reuse detection, reached through a new method."""
        created = await register(auth_client, unique_email("refresh"))
        original = created["session"]["refresh_token"]

        rotated = await auth_client.post("/v1/auth/refresh", json={"refresh_token": original})
        assert rotated.status_code == 200
        assert rotated.json()["refresh_token"] != original

        reused = await auth_client.post("/v1/auth/refresh", json={"refresh_token": original})
        assert reused.status_code == 401

        # Reuse revokes the whole session, so the replacement dies with it.
        after = await auth_client.post(
            "/v1/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}
        )
        assert after.status_code == 401

    async def test_a_google_session_logs_out_of_every_device(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str], system_db: AsyncSession
    ) -> None:
        email = unique_email("logout")
        token = google_token(subject="g-logout", email=email)
        one = await auth_client.post(
            "/v1/auth/oauth/google", json={"id_token": token, "device": {"install_id": "d-1"}}
        )
        two = await auth_client.post(
            "/v1/auth/oauth/google", json={"id_token": token, "device": {"install_id": "d-2"}}
        )
        assert one.json()["user_id"] == two.json()["user_id"]

        out = await auth_client.post(
            "/v1/auth/logout",
            json={"all_devices": True},
            headers={"Authorization": f"Bearer {two.json()['access_token']}"},
        )
        assert out.status_code == 204

        live = (
            await system_db.execute(
                sa.select(sa.func.count())
                .select_from(AuthSession)
                .where(
                    AuthSession.user_id == uuid.UUID(one.json()["user_id"]),
                    AuthSession.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        assert live == 0

    async def test_every_method_reaches_the_same_onboarding_answer(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        password = (await register(auth_client, unique_email("onboard-p")))["session"]
        google = (
            await auth_client.post(
                "/v1/auth/oauth/google",
                json={"id_token": google_token(subject="g-onboard", email=unique_email("g"))},
            )
        ).json()
        apple = (
            await auth_client.post(
                "/v1/auth/oauth/apple",
                json={"id_token": apple_token(subject="a-onboard", email=unique_email("a"))},
            )
        ).json()
        for session in (password, google, apple):
            assert session["needs_onboarding"] is True
            assert session["tenant_id"] is None
            assert session["tenants"] == []
            headers = {"Authorization": f"Bearer {session['access_token']}"}
            me = await auth_client.get("/v1/me", headers=headers)
            assert me.status_code == 200, me.text
            assert me.json()["masked_phone"] is None
            shop = await auth_client.post(
                "/v1/tenants", json={"name": "Auth test shop"}, headers=headers
            )
            assert shop.status_code == 201, shop.text
            assert shop.json()["tenant_id"] is not None
            assert shop.json()["user_id"] == session["user_id"]
            # No credentials are reissued: the shop is bound to the existing
            # session on the server, so the token already held now sees it.
            assert "access_token" not in shop.json()
            assert "refresh_token" not in shop.json()
            me = await auth_client.get("/v1/me", headers=headers)
            assert me.status_code == 200, me.text
            tenant = await auth_client.get("/v1/tenant", headers=headers)
            assert tenant.status_code == 200, tenant.text
            assert tenant.json()["id"] == shop.json()["tenant_id"]
            assert me.json()["tenant_id"] == shop.json()["tenant_id"]
            assert me.json()["role"] == "OWNER"


class TestEmailLinks:
    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_deployed_responses_never_expose_link_tokens(
        self, settings: Any, environment: str
    ) -> None:
        from app.api.v1.auth import _debug_token
        from app.core.config import AppEnv

        deployed = settings.model_copy(update={"app_env": AppEnv(environment)})
        assert _debug_token(deployed, "secret-link-token") is None

    async def test_delivered_links_verify_email_and_reset_password(
        self,
        auth_client: AsyncClient,
        unique_email: Callable[[str], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from urllib.parse import parse_qs, urlsplit

        from app.api import deps
        from app.notifications.transport import MockEmailTransport

        transport = MockEmailTransport()
        monkeypatch.setattr(deps, "_email_transport", transport)
        email = unique_email("mail")
        created = await register(auth_client, email)
        assert created["email_verification_sent"] is True
        verify_link = next(
            line for line in transport.sent[-1].text_body.splitlines() if "#token=" in line
        )
        parsed = urlsplit(verify_link)
        assert not parsed.query
        assert (await auth_client.get(parsed.path)).status_code == 200
        verification = parse_qs(parsed.fragment)["token"][0]
        assert (
            await auth_client.post("/v1/auth/email/verify", json={"token": verification})
        ).status_code == 200
        await auth_client.post("/v1/auth/password/forgot", json={"email": email})
        reset_link = next(
            line for line in transport.sent[-1].text_body.splitlines() if "#token=" in line
        )
        reset = urlsplit(reset_link)
        assert reset.path == "/auth/password/reset"
        assert (
            await auth_client.post(
                "/v1/auth/password/reset",
                json={
                    "token": parse_qs(reset.fragment)["token"][0],
                    "new_password": "mail-new-password",
                },
            )
        ).status_code == 200
        assert (
            await auth_client.post(
                "/v1/auth/login", json={"email": email, "password": "mail-new-password"}
            )
        ).status_code == 200

    @pytest.mark.parametrize("path", ["email/verify", "password/reset"])
    async def test_links_have_safe_browser_landing_pages(
        self, auth_client: AsyncClient, path: str
    ) -> None:
        response = await auth_client.get(f"/auth/{path}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert f'data-endpoint="/v1/auth/{path}"' in response.text

    async def test_simultaneous_reset_requests_consume_a_token_only_once(
        self, auth_client: AsyncClient, unique_email: Callable[[str], str]
    ) -> None:
        email = unique_email("concurrent-reset")
        await register(auth_client, email)
        forgot = await auth_client.post("/v1/auth/password/forgot", json={"email": email})
        token = forgot.json()["debug_token"]
        results = await asyncio.gather(
            *(
                auth_client.post(
                    "/v1/auth/password/reset",
                    json={"token": token, "new_password": f"concurrent-password-{i}"},
                )
                for i in range(2)
            )
        )
        assert sorted(response.status_code for response in results) == [200, 401]


class TestJwksCache:
    async def test_expired_keys_are_not_trusted_after_refresh_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(503))
        monkeypatch.setattr(
            "app.auth.oidc.httpx.AsyncClient",
            lambda **kwargs: real_client(**kwargs, transport=transport),
        )
        cache = JwksCache(ttl_seconds=0)
        cache.seed(GOOGLE_JWKS_URI, _jwks())
        with pytest.raises(AuthenticationError):
            await cache.get(GOOGLE_JWKS_URI)

    async def test_unknown_keys_do_not_trigger_a_fetch_on_every_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = []

        def respond(request: httpx.Request) -> httpx.Response:
            attempts.append(request)
            return httpx.Response(200, json=_jwks())

        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            "app.auth.oidc.httpx.AsyncClient",
            lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)),
        )
        cache = JwksCache()
        cache.seed(GOOGLE_JWKS_URI, _jwks())
        verifier = OidcTokenVerifier(
            provider=AuthProvider.GOOGLE,
            issuers=("https://accounts.google.com",),
            jwks_uri=GOOGLE_JWKS_URI,
            audiences=(GOOGLE_AUDIENCE,),
            jwks=cache,
        )
        for kid in ("unknown-1", "unknown-2"):
            token = jwt.encode({"sub": "unknown"}, _KEY, algorithm="RS256", headers={"kid": kid})
            with pytest.raises(AuthenticationError):
                await verifier.verify(token)
        assert len(attempts) == 1


class TestDisabledMethods:
    async def test_password_sign_in_refuses_when_the_flag_is_off(
        self, settings: Any, unique_email: Callable[[str], str]
    ) -> None:
        """The boot flag is the switch, and it is checked in the service."""
        from app.api.deps import get_app_settings, reset_singletons
        from app.main import create_app

        disabled = settings.model_copy(update={"email_password_auth_enabled": False})
        reset_singletons()
        app = create_app(disabled)
        app.dependency_overrides[get_app_settings] = lambda: disabled
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
            app.router.lifespan_context(app),
        ):
            response = await client.post(
                "/v1/auth/login",
                json={"email": unique_email("off"), "password": PASSWORD},
            )
        app.dependency_overrides.clear()
        reset_singletons()
        assert response.status_code == 403
        assert response.json()["code"] == ErrorCode.FEATURE_DISABLED
