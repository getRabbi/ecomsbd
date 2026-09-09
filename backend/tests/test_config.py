"""Configuration safety.

Master spec section 68: startup must fail fast when a critical production
secret is missing. The tests that matter most here are the ones proving the
development OTP provider cannot reach production.
"""

from __future__ import annotations

import base64

import pytest

from app.core.config import AppEnv, OtpProvider, Settings

REAL_SECRET = "x" * 48
REAL_KEY = base64.b64encode(b"k" * 32).decode()


def production_settings(**overrides: object) -> Settings:
    """A production configuration that is valid unless a test breaks it."""
    base: dict[str, object] = {
        "app_env": AppEnv.PRODUCTION,
        "public_base_url": "https://api.example.com",
        "database_url": "postgresql+asyncpg://u:p@db:5432/ecomsbd",
        "redis_url": "redis://redis:6379/0",
        "jwt_signing_key": REAL_SECRET,
        "otp_hash_secret": REAL_SECRET,
        "phone_search_hmac_key": REAL_SECRET,
        "credential_encryption_key": REAL_KEY,
        "otp_provider": OtpProvider.SMS_GATEWAY,
        "otp_provider_secret": "gateway-secret",
        "allow_dev_otp": False,
        "otp_expose_debug_code": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestLocalDefaults:
    def test_local_boots_with_no_configuration(self) -> None:
        settings = Settings(app_env=AppEnv.LOCAL)
        assert settings.app_name == "ecomsbd"
        assert settings.default_timezone == "Asia/Dhaka"

    def test_otp_rules_match_the_spec(self) -> None:
        settings = Settings(app_env=AppEnv.LOCAL)
        assert settings.otp_length == 6
        assert settings.otp_ttl_seconds == 300  # 5 minutes
        assert settings.otp_max_attempts == 5


class TestProductionGuards:
    def test_development_defaults_are_rejected(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            Settings(app_env=AppEnv.PRODUCTION)
        message = str(excinfo.value)
        assert "JWT_SIGNING_KEY" in message
        assert "CREDENTIAL_ENCRYPTION_KEY" in message

    def test_a_correct_production_config_is_accepted(self) -> None:
        settings = production_settings()
        assert settings.app_env.is_production
        assert settings.dev_otp_enabled is False
        assert settings.expose_otp_debug_code is False

    def test_dev_otp_provider_cannot_be_selected(self) -> None:
        with pytest.raises(ValueError, match="dev_console is forbidden in production"):
            production_settings(otp_provider=OtpProvider.DEV_CONSOLE, otp_provider_secret=None)

    def test_allow_dev_otp_flag_is_blocked_independently(self) -> None:
        # Second guard: even with a real gateway selected, the dev switch is refused.
        with pytest.raises(ValueError, match="ALLOW_DEV_OTP must be false"):
            production_settings(allow_dev_otp=True)

    def test_debug_code_exposure_is_blocked_independently(self) -> None:
        with pytest.raises(ValueError, match="OTP_EXPOSE_DEBUG_CODE must be false"):
            production_settings(otp_expose_debug_code=True)

    def test_https_is_required(self) -> None:
        with pytest.raises(ValueError, match="must use https"):
            production_settings(public_base_url="http://api.example.com")

    def test_sqlite_is_refused(self) -> None:
        with pytest.raises(ValueError, match="SQLite is not a supported production database"):
            production_settings(database_url="sqlite+aiosqlite:///./x.db")

    def test_redis_is_required_in_deployed_environments(self) -> None:
        with pytest.raises(ValueError, match="REDIS_URL is required"):
            production_settings(redis_url=None)

    def test_staging_also_refuses_development_secrets(self) -> None:
        with pytest.raises(ValueError, match="development placeholder"):
            Settings(app_env=AppEnv.STAGING, redis_url="redis://r:6379/0")

    def test_short_secrets_are_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 32 characters"):
            production_settings(jwt_signing_key="tooshort")

    def test_sms_gateway_requires_its_secret(self) -> None:
        with pytest.raises(ValueError, match="OTP_PROVIDER_SECRET is required"):
            Settings(app_env=AppEnv.LOCAL, otp_provider=OtpProvider.SMS_GATEWAY)


class TestCredentialKey:
    def test_key_must_be_32_bytes(self) -> None:
        with pytest.raises(ValueError, match="exactly 32 bytes"):
            Settings(
                app_env=AppEnv.LOCAL,
                credential_encryption_key=base64.b64encode(b"short").decode(),
            )

    def test_key_must_be_base64(self) -> None:
        with pytest.raises(ValueError, match="base64"):
            Settings(app_env=AppEnv.LOCAL, credential_encryption_key="not base64 !!!")


class TestSanitizedSnapshot:
    def test_secrets_are_never_rendered(self) -> None:
        snapshot = production_settings().sanitized()
        assert snapshot["jwt_signing_key"] == "<set>"
        assert snapshot["credential_encryption_key"] == "<set>"
        assert snapshot["app_env"] == AppEnv.PRODUCTION
        assert REAL_SECRET not in str(snapshot)
