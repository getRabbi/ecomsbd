"""Focused seller-auth boundary tests; no provider network or business regression suite."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from unittest.mock import AsyncMock

import jwt
import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.auth.identities import AuthIdentity, AuthProvider
from app.auth.supabase import SupabaseVerifier, resolve_session, resolve_user
from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError
from app.core.security import TokenService
from app.db.session import get_sessionmaker
from app.main import create_app
from app.tenants.models import TenantUser
from app.users.models import User


@pytest.fixture
def supabase(settings, monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="current", alg="ES256", use="sig")
    verifier = SupabaseVerifier(settings)
    verifier._keys = [jwk]
    verifier._fetched = time.monotonic()
    verifier.verified_email = AsyncMock(return_value=f"{uuid.uuid4()}@example.com")
    monkeypatch.setattr(deps, "_supabase_verifier", verifier)
    subject, session = uuid.uuid4(), uuid.uuid4()

    def token(**overrides):
        claims = {
            "iss": verifier.issuer,
            "aud": "authenticated",
            "sub": str(subject),
            "session_id": str(session),
            "role": "authenticated",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        }
        claims.update(overrides)
        return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "current"})

    return verifier, token


async def test_valid_jwt_and_claim_failures(supabase):
    verifier, token = supabase
    assert (await verifier.verify(token())).subject
    for changes in [
        {"exp": int(time.time()) - 1},
        {"iss": "https://wrong.supabase.co/auth/v1"},
        {"aud": "wrong"},
        {"sub": "bad"},
        {"role": "service_role"},
        {"is_anonymous": True},
        {"session_id": ""},
        {"iat": int(time.time()) + 900},
    ]:
        with pytest.raises(AuthenticationError):
            await verifier.verify(token(**changes))
    forged = token().split(".")
    forged[2] = "A" * len(forged[2])
    with pytest.raises(AuthenticationError):
        await verifier.verify(".".join(forged))
    with pytest.raises(AuthenticationError):
        await verifier.verify(jwt.encode({"sub": str(uuid.uuid4())}, "bad", algorithm="HS256"))


async def test_rotation_and_stale_cache_fail_closed(supabase, monkeypatch):
    verifier, token = supabase
    keys = verifier._keys
    verifier._keys = []
    original = verifier._fetch_keys

    async def rotated(*, force=False):
        return keys if force else []

    monkeypatch.setattr(verifier, "_fetch_keys", rotated)
    assert await verifier.verify(token())
    monkeypatch.setattr(verifier, "_fetch_keys", original)
    verifier._fetched = time.monotonic() - 601
    verifier._attempted = time.monotonic()
    with pytest.raises(AuthenticationError):
        await verifier.verify(token())


async def test_mapping_verified_link_and_ambiguous_conflict(db, supabase):
    verifier, token = supabase
    raw = token()
    claims = await verifier.verify(raw)
    user = await resolve_user(db, verifier, raw, claims)
    assert (await resolve_user(db, verifier, raw, claims)).id == user.id
    assert verifier.verified_email.await_count == 1
    other_claims = await verifier.verify(token(sub=str(uuid.uuid4())))
    with pytest.raises(ConflictError):
        await resolve_user(db, verifier, raw, other_claims)


@pytest.mark.parametrize("verified", [True, False])
async def test_link_requires_verified_legacy_ownership(db, supabase, verified):
    verifier, token = supabase
    user = User()
    db.add(user)
    await db.flush()
    email = verifier.verified_email.return_value
    db.add(
        AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.PASSWORD,
            provider_subject=email,
            normalized_email=email,
            email_verified=verified,
        )
    )
    await db.flush()
    raw = token()
    claims = await verifier.verify(raw)
    if verified:
        assert (await resolve_user(db, verifier, raw, claims)).id == user.id
    else:
        with pytest.raises(ConflictError):
            await resolve_user(db, verifier, raw, claims)


async def test_concurrent_first_login_one_user(settings, supabase):
    if settings.is_sqlite:
        pytest.skip("Concurrency is validated against isolated local PostgreSQL")
    verifier, token = supabase
    raw = token()
    claims = await verifier.verify(raw)
    factory = get_sessionmaker(settings)

    async def login():
        async with factory() as db:
            user = await resolve_user(db, verifier, raw, claims)
            session = await resolve_session(db, claims, user)
            await db.commit()
            return user.id, session.id

    results = await asyncio.gather(*(login() for _ in range(6)))
    assert len(set(results)) == 1
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    sa.select(AuthIdentity).where(
                        AuthIdentity.provider_subject == str(claims.subject),
                        AuthIdentity.provider == AuthProvider.SUPABASE,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


async def test_api_mapping_onboarding_rbac_and_retired_endpoints(settings, supabase):
    _verifier, token = supabase
    app = create_app(settings)
    # Requests resolve settings through `get_app_settings`, the process-wide
    # singleton. Without this override the app would run on whatever an
    # earlier test left there instead of this fixture's Supabase settings.
    app.dependency_overrides[deps.get_app_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        headers = {"Authorization": f"Bearer {token()}"}
        response = await client.get("/v1/me", headers=headers)
        assert response.status_code == 200, response.text
        user_id = response.json()["user_id"]
        assert (await client.get("/v1/me", headers=headers)).json()["user_id"] == user_id
        attached = await client.post(
            "/v1/auth/device",
            headers=headers,
            json={
                "install_id": str(uuid.uuid4()),
                "platform": "ANDROID",
                "push_token": "test-push-registration-token",
            },
        )
        assert attached.status_code == 204
        shop = await client.post(
            "/v1/tenants",
            headers=headers,
            json={
                "name": "Auth test shop",
                "business_category": "OTHER",
                "pickup_contact_name": "Seller",
                "pickup_phone": "01712345678",
                "pickup_address": "Dhaka",
                "pickup_district": "Dhaka",
                "pickup_area": "Dhanmondi",
            },
        )
        assert shop.status_code == 201, shop.text
        assert "access_token" not in shop.json()
        me = (await client.get("/v1/me", headers=headers)).json()
        assert me["tenant_id"] == shop.json()["tenant_id"]
        assert me["role"] == "OWNER"
        # RBAC reads current membership, not client/JWT metadata.
        from app.db.tenancy import mark_session_system

        async with get_sessionmaker(settings)() as db:
            mark_session_system(db.sync_session, "auth test role update")
            membership = (
                await db.execute(
                    sa.select(TenantUser).where(
                        TenantUser.user_id == uuid.UUID(user_id),
                    )
                )
            ).scalar_one()
            membership.role = "VIEWER"
            await db.commit()
        denied = await client.patch("/v1/tenant", headers=headers, json={"name": "Denied"})
        assert denied.status_code == 403, denied.text
        for path in [
            "login",
            "register",
            "refresh",
            "otp/request",
            "otp/verify",
            "email/verify",
            "email/resend",
            "password/forgot",
            "password/reset",
            "password/change",
            "oauth/google",
            "oauth/apple",
        ]:
            response = await client.post("/v1/auth/" + path, json={})
            assert response.status_code == 410, (path, response.text)
        assert (await client.post("/v1/auth/logout", headers=headers, json={})).status_code == 204
        from app.auth.models import Device

        async with get_sessionmaker(settings)() as db:
            registered = (
                await db.execute(sa.select(Device).where(Device.user_id == uuid.UUID(user_id)))
            ).scalar_one()
            assert registered.push_token is None
        assert (await client.get("/v1/me", headers=headers)).status_code == 401


def test_no_deployed_custom_issuer(supabase, settings):
    with pytest.raises(Exception, match="Supabase"):
        TokenService(settings).issue_access_token(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            tenant_id=None,
            role=None,
        )


def test_production_auth_requires_supabase_only():
    import base64

    cfg = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://test:test@db/test",
        redis_url="redis://redis:6379",
        public_base_url="https://api.scalemyprints.com",
        supabase_url="https://project.supabase.co",
        supabase_anon_key="public-key",
        r2_bucket="ecomsbd-production",
        r2_endpoint_url="https://test.r2.cloudflarestorage.com",
        phone_search_hmac_key="p" * 40,
        jwt_signing_key="a" * 40,
        credential_encryption_key=base64.b64encode(b"x" * 32).decode(),
        phone_otp_login_enabled=False,
        allow_dev_otp=False,
        otp_expose_debug_code=False,
    )
    assert cfg.supabase_auth_active
    assert cfg.enabled_auth_methods == {"email_password", "google", "apple"}
