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
#: Shaped like an Apple ``.p8`` and cryptographically worthless. The validator
#: checks for the PEM header, so what matters here is the shape, not the bytes.
APPLE_KEY = "-----BEGIN PRIVATE KEY-----\nbm90LWEtcmVhbC1rZXk=\n-----END PRIVATE KEY-----\n"


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
        # Production authenticates through Supabase, and refuses to boot
        # without it. Phone OTP login is the pre-Supabase path and must be off.
        "supabase_url": "https://project-ref.supabase.co",
        "supabase_anon_key": "public-anon-key",
        "phone_otp_login_enabled": False,
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
        # The provider guard only matters when OTP sign-in is on; with it off
        # nothing ever calls a provider. Turning it on is refused in its own
        # right under Supabase, and the dev provider is still named as a problem.
        with pytest.raises(ValueError, match="dev_console is forbidden in production"):
            production_settings(
                phone_otp_login_enabled=True,
                otp_provider=OtpProvider.DEV_CONSOLE,
                otp_provider_secret=None,
            )

    def test_allow_dev_otp_flag_is_blocked_independently(self) -> None:
        # Second guard: even with a real gateway selected, the dev switch is refused.
        with pytest.raises(ValueError, match="ALLOW_DEV_OTP must be false"):
            production_settings(phone_otp_login_enabled=True, allow_dev_otp=True)

    def test_phone_otp_login_is_refused_under_supabase(self) -> None:
        with pytest.raises(ValueError, match="PHONE_OTP_LOGIN_ENABLED must be false"):
            production_settings(phone_otp_login_enabled=True)

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


class TestBlankValues:
    """An operator filling in a template leaves lines blank. Blank means unset.

    The failure this prevents is quiet: ``REDIS_URL=`` is the empty *string*,
    which is not ``None``, so a presence check passes and the problem surfaces
    on the first request instead of at startup.
    """

    def test_a_blank_required_secret_is_refused_in_production(self) -> None:
        with pytest.raises(ValueError, match="JWT_SIGNING_KEY is not set"):
            production_settings(jwt_signing_key="")

    def test_a_blank_redis_url_is_refused_in_production(self) -> None:
        with pytest.raises(ValueError, match="REDIS_URL is required"):
            production_settings(redis_url="")

    def test_a_blank_database_url_is_refused_in_production(self) -> None:
        with pytest.raises(ValueError, match="DATABASE_URL is not set"):
            production_settings(database_url="")

    def test_a_blank_optional_value_is_simply_absent(self) -> None:
        settings = production_settings(sentry_dsn="", support_email="", public_web_url="")
        assert settings.sentry_dsn is None
        assert settings.support_email is None
        assert settings.public_web_url is None

    def test_a_blank_list_is_empty_not_a_parse_error(self) -> None:
        settings = production_settings(cors_allow_origins="", trusted_hosts="")
        assert settings.cors_allow_origins == []
        assert settings.trusted_hosts == []

    def test_a_list_may_be_comma_separated_or_json(self) -> None:
        comma = production_settings(
            cors_allow_origins="https://a.example.com,https://b.example.com"
        )
        json_form = production_settings(
            cors_allow_origins='["https://a.example.com", "https://b.example.com"]'
        )
        assert comma.cors_allow_origins == json_form.cors_allow_origins
        assert len(comma.cors_allow_origins) == 2

    def test_a_blank_mapping_is_empty(self) -> None:
        assert production_settings(play_product_plan_map="").play_product_plan_map == {}


class TestAuthMethods:
    """Sign-in must be configured and reachable without an SMS gateway."""

    def test_the_intended_production_auth_model_boots(self) -> None:
        settings = production_settings(
            phone_otp_login_enabled=False,
            otp_provider=OtpProvider.SMS_GATEWAY,
            otp_provider_secret=None,
            email_password_auth_enabled=True,
            google_auth_enabled=True,
            google_client_id_web="123.apps.googleusercontent.com",
            apple_auth_enabled=True,
            apple_client_id="com.example.app",
            email_transport="provider_api",
            email_api_base_url="https://api.email.example.com",
            email_api_key="k" * 32,
            email_from_address="no-reply@example.com",
        )
        assert settings.available_auth_methods == {"email_password", "google", "apple"}
        assert not settings.dev_otp_enabled

    def test_production_sign_in_is_supabase_and_cannot_be_switched_off(self) -> None:
        # Production authenticates through Supabase, so the legacy per-method
        # switches cannot leave a deployment with no way in.
        settings = production_settings(
            phone_otp_login_enabled=False,
            email_password_auth_enabled=False,
            google_auth_enabled=False,
            apple_auth_enabled=False,
        )
        assert settings.supabase_auth_active
        assert settings.available_auth_methods == frozenset({"email_password", "google", "apple"})

    def test_a_production_config_without_supabase_is_refused(self) -> None:
        with pytest.raises(ValueError, match="SUPABASE_ANON_KEY is required"):
            production_settings(supabase_anon_key=None)
        with pytest.raises(ValueError, match="SUPABASE_URL must be the HTTPS project origin"):
            production_settings(supabase_url="http://project-ref.supabase.co")

    def test_local_is_not_subject_to_the_sign_in_check(self) -> None:
        # A developer working on something unrelated may turn every method off.
        settings = Settings(app_env=AppEnv.LOCAL, phone_otp_login_enabled=False)
        assert settings.available_auth_methods == frozenset()

    def test_a_disabled_otp_login_does_not_demand_a_gateway(self) -> None:
        """Credentials are required for what is switched on, and nothing else.

        The configuration is still refused — nothing implemented is enabled —
        but the reported problem must be the one that is true. Being told to
        supply an SMS gateway key for a sign-in route that answers
        FEATURE_DISABLED is how an operator learns to fill in values blindly.
        """
        settings = production_settings(
            phone_otp_login_enabled=False,
            otp_provider=OtpProvider.SMS_GATEWAY,
            otp_provider_secret=None,
        )
        assert settings.otp_provider_secret is None

    # In production Google and Apple are Supabase providers, configured in the
    # Supabase dashboard. The backend's own verifiers remain for environments
    # without Supabase, and there an audience is still mandatory.

    def test_google_enabled_without_an_audience_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no Google client id is set"):
            Settings(app_env=AppEnv.LOCAL, google_auth_enabled=True)

    def test_apple_enabled_without_its_audience_is_refused(self) -> None:
        with pytest.raises(ValueError, match="APPLE_CLIENT_ID"):
            Settings(
                app_env=AppEnv.LOCAL,
                apple_auth_enabled=True,
                apple_team_id="ABCDE12345",
                apple_key_id="FGHIJ67890",
            )

    def test_an_apple_key_path_is_rejected_in_favour_of_the_contents(self) -> None:
        with pytest.raises(ValueError, match="PEM"):
            production_settings(apple_private_key="/etc/secrets/AuthKey_ABC123.p8")

    def test_an_apple_key_with_escaped_newlines_is_accepted(self) -> None:
        settings = production_settings(apple_private_key=APPLE_KEY.replace("\n", "\\n"))
        assert settings.apple_private_key is not None
        assert "\n" in settings.apple_private_key.get_secret_value()

    def test_the_google_audience_set_collects_every_configured_client(self) -> None:
        settings = production_settings(
            google_client_id_android="a.apps.googleusercontent.com",
            google_client_id_web="w.apps.googleusercontent.com",
        )
        assert len(settings.google_client_ids) == 2


class TestEmailTransport:
    def test_the_console_transport_cannot_reach_production(self) -> None:
        """A reset email body is a credential; the console transport prints it."""
        with pytest.raises(ValueError, match="EMAIL_TRANSPORT=console is forbidden"):
            production_settings(email_transport="console")

    def test_the_mock_transport_cannot_reach_production(self) -> None:
        with pytest.raises(ValueError, match="EMAIL_TRANSPORT=mock is forbidden"):
            production_settings(email_transport="mock")

    def test_a_provider_api_without_credentials_is_refused(self) -> None:
        with pytest.raises(ValueError, match="EMAIL_TRANSPORT=provider_api requires"):
            production_settings(email_transport="provider_api")

    def test_supabase_delivers_sign_in_mail_in_production(self) -> None:
        # Verification and reset mail for email/password sign-in is sent by
        # Supabase, so the backend's own transport is not demanded for it.
        settings = production_settings(email_password_auth_enabled=True)
        assert not settings.email_transport_can_deliver
        assert "email_password" in settings.available_auth_methods

    def test_a_configured_provider_is_accepted(self) -> None:
        settings = production_settings(
            email_transport="provider_api",
            email_api_base_url="https://api.email.example.com",
            email_api_key="k" * 32,
            email_from_address="no-reply@example.com",
        )
        assert settings.email_transport_can_deliver


class TestPublicSurface:
    def test_a_wildcard_cors_origin_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not contain"):
            production_settings(cors_allow_origins=["*"])

    def test_development_origins_are_refused(self) -> None:
        with pytest.raises(ValueError, match="development origins"):
            production_settings(cors_allow_origins=["http://localhost:3000"])

    def test_a_plain_http_origin_is_refused_in_production(self) -> None:
        with pytest.raises(ValueError, match="must use https"):
            production_settings(cors_allow_origins=["http://app.example.com"])

    def test_a_wildcard_trusted_host_is_refused(self) -> None:
        with pytest.raises(ValueError, match="TRUSTED_HOSTS must not be"):
            production_settings(trusted_hosts=["*"])

    def test_a_localhost_base_url_is_refused_even_over_https(self) -> None:
        with pytest.raises(ValueError, match="still points at localhost"):
            production_settings(public_base_url="https://localhost:8000")

    def test_a_hostname_merely_containing_localhost_is_fine(self) -> None:
        settings = production_settings(public_base_url="https://localhost-shop.example.com")
        assert settings.public_base_url.endswith("example.com")


class TestPartialIntegrations:
    """A half-configured integration fails on the first seller who needs it."""

    def test_partial_r2_is_refused(self) -> None:
        # Credentials without somewhere to use them fail at the first upload.
        with pytest.raises(ValueError, match="R2 is partly configured"):
            production_settings(r2_bucket="ecomsbd-prod", r2_access_key_id="id")

    def test_a_bucket_awaiting_credentials_does_not_block(self) -> None:
        settings = production_settings(r2_bucket="ecomsbd-prod")
        assert not settings.r2_configured

    def test_complete_r2_is_accepted(self) -> None:
        settings = production_settings(
            r2_endpoint_url="https://acct.r2.cloudflarestorage.com",
            r2_bucket="ecomsbd-prod",
            r2_access_key_id="id",
            r2_secret_access_key="secret",
        )
        assert settings.r2_configured

    def test_partial_bkash_is_refused(self) -> None:
        with pytest.raises(ValueError, match="bKash is partly configured"):
            production_settings(bkash_base_url="https://bkash.example.com")

    def test_no_bkash_configuration_is_fine(self) -> None:
        """A disabled integration must never require its credentials."""
        assert production_settings().bkash_billing_configured is False

    def test_fcm_credentials_without_a_project_are_refused(self) -> None:
        with pytest.raises(ValueError, match="FCM_PROJECT_ID is required"):
            production_settings(fcm_credentials_json='{"type":"service_account"}')


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
