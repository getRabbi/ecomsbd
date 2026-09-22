"""The production configuration check, and the email transport it reports on.

Two claims are under test.

**The check never prints a value.** Every test that builds a configuration uses
distinctive strings for its credentials and asserts none of them appears in the
output. This is the property that makes the command safe to run with someone
watching, and it is worth an assertion rather than a convention.

**A disabled integration is not a failure.** The exit code is about whether
this configuration is safe to start, not about whether every provider has been
signed up for. Missing Sentry, missing R2 and a disabled bKash all exit zero.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.check_production_config import Status, main, run_checks
from app.core.config import AppEnv, OtpProvider, Settings
from app.core.redaction import mask_email
from app.notifications.transport import (
    EMAIL_BLOCKER,
    ConsoleEmailTransport,
    DeliveryOutcome,
    DisabledEmailTransport,
    EmailMessage,
    MockEmailTransport,
    ProviderApiEmailTransport,
    build_email_transport,
)

#: Distinctive enough that finding one in the output is unambiguous.
SECRET_MARKER = "zzTOPSECRETvaluezz"
REAL_KEY = base64.b64encode(b"k" * 32).decode()


def production_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": AppEnv.PRODUCTION,
        "public_base_url": "https://api.example.com",
        "trusted_hosts": ["api.example.com"],
        "database_url": f"postgresql+asyncpg://u:{SECRET_MARKER}@db:5432/ecomsbd",
        "redis_url": f"rediss://:{SECRET_MARKER}@redis:6379/0",
        "jwt_signing_key": f"jwt-{SECRET_MARKER}-padding-to-thirty-two-chars",
        "otp_hash_secret": f"otp-{SECRET_MARKER}-padding-to-thirty-two-chars",
        "phone_search_hmac_key": f"hmac-{SECRET_MARKER}-padding-to-thirty-two",
        "credential_encryption_key": REAL_KEY,
        "otp_provider": OtpProvider.SMS_GATEWAY,
        "otp_provider_secret": SECRET_MARKER,
        "allow_dev_otp": False,
        "otp_expose_debug_code": False,
        # Production authenticates through Supabase, and refuses to boot
        # without it. Phone OTP login is the pre-Supabase path and must be off.
        "supabase_url": "https://project-ref.supabase.co",
        "supabase_anon_key": f"anon-{SECRET_MARKER}",
        "phone_otp_login_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def status_for(area: str, settings: Settings) -> Status:
    return next(result.status for result in run_checks(settings) if result.area == area)


class TestNoSecretsArePrinted:
    def test_no_configured_value_reaches_the_output(self) -> None:
        results = run_checks(production_settings())
        rendered = "\n".join(result.render() for result in results)
        assert SECRET_MARKER not in rendered

    def test_an_invalid_configuration_reports_rules_not_values(
        self,
        capsys,  # type: ignore[no-untyped-def]
        tmp_path,  # type: ignore[no-untyped-def]
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A rejected DATABASE_URL carries a password. It must not be echoed."""
        # The suite's PostgreSQL URL otherwise overrides this deliberately
        # invalid file value, as real environment variables correctly win.
        monkeypatch.delenv("DATABASE_URL", raising=False)
        env_file = tmp_path / "broken.env"
        env_file.write_text(
            "APP_ENV=production\n"
            f"DATABASE_URL=mysql://user:{SECRET_MARKER}@db/ecomsbd\n"
            "REDIS_URL=redis://r:6379/0\n",
            encoding="utf-8",
        )
        exit_code = main(["--env-file", str(env_file), "--env", "production"])
        output = capsys.readouterr().out
        assert exit_code == 1
        assert SECRET_MARKER not in output
        assert "DATABASE_URL must be a PostgreSQL URL" in output


class TestExitCode:
    def test_a_workable_production_configuration_exits_zero(self) -> None:
        results = run_checks(production_settings())
        assert not [result for result in results if result.blocking]

    def test_missing_optional_integrations_do_not_block(self) -> None:
        settings = production_settings()
        assert status_for("SENTRY", settings) is Status.OPTIONAL
        assert status_for("R2", settings) is Status.MISSING
        assert status_for("BKASH", settings) is Status.DISABLED
        assert status_for("FCM", settings) is Status.DISABLED
        assert status_for("PLAY BILLING", settings) is Status.DISABLED

    def test_reusing_one_secret_for_two_purposes_blocks(self) -> None:
        """Four names holding one value is one secret wearing four hats."""
        shared = "s" * 48
        settings = production_settings(jwt_signing_key=shared, phone_search_hmac_key=shared)
        assert status_for("CRYPTO", settings) is Status.BLOCKING

    def test_an_unsigned_placeholder_play_package_blocks(self) -> None:
        settings = production_settings(
            play_package_name="com.example.ecomsbd",
            play_service_account_json='{"type":"service_account"}',
            play_product_plan_map={"sku": "pro"},
        )
        assert status_for("PLAY BILLING", settings) is Status.BLOCKING

    def test_steadfast_reports_ready_without_any_merchant_credential(self) -> None:
        """There is deliberately nothing to fill in: keys are per seller."""
        result = next(r for r in run_checks(production_settings()) if r.area == "STEADFAST")
        assert result.status is Status.OK
        assert "READY FOR MERCHANT ACCOUNT" in result.detail

    def test_google_and_apple_are_reported_as_supabase_providers(self) -> None:
        """Google is a Supabase provider the operator verifies in the dashboard;
        Apple is deferred until iOS. Neither is claimed ready from env vars."""
        settings = production_settings(
            google_auth_enabled=True,
            google_client_id_web="web.apps.googleusercontent.com",
            apple_auth_enabled=True,
            apple_client_id="com.example.app",
            phone_otp_login_enabled=False,
            otp_provider_secret=None,
        )
        assert status_for("GOOGLE AUTH", settings) is Status.PARTIAL
        assert status_for("APPLE AUTH", settings) is Status.DISABLED
        assert status_for("SIGN-IN", settings) is Status.OK
        assert status_for("PHONE OTP", settings) is Status.DISABLED

    def test_an_empty_trusted_host_list_is_reported_but_not_fatal(self) -> None:
        settings = production_settings(trusted_hosts=[])
        assert status_for("URLS / CORS", settings) is Status.MISSING

    def test_the_shipped_local_defaults_are_reported_as_local(self) -> None:
        settings = Settings(app_env=AppEnv.LOCAL)
        assert status_for("DATABASE", settings) is Status.OK
        assert not [result for result in run_checks(settings) if result.blocking]


class TestEmailTransports:
    async def test_the_shipped_default_reports_not_configured(self) -> None:
        result = await DisabledEmailTransport().send(
            EmailMessage(to_address="seller@example.com", subject="Hi", text_body="Body")
        )
        assert result.outcome is DeliveryOutcome.NOT_CONFIGURED
        assert result.detail == EMAIL_BLOCKER
        assert result.delivered is False

    async def test_a_provider_api_validates_before_it_checks_configuration(self) -> None:
        """A malformed message must fail the same way configured or not."""
        transport = ProviderApiEmailTransport(Settings(app_env=AppEnv.LOCAL))
        result = await transport.send(
            EmailMessage(to_address="not-an-address", subject="Hi", text_body="Body")
        )
        assert result.outcome is DeliveryOutcome.REJECTED
        assert "valid email address" in (result.detail or "")

    async def test_a_newline_in_the_subject_is_refused(self) -> None:
        """Header injection: a newline lets the subject append a Bcc."""
        transport = MockEmailTransport()
        result = await transport.send(
            EmailMessage(
                to_address="seller@example.com",
                subject="Reset\nBcc: attacker@example.com",
                text_body="Body",
            )
        )
        assert result.outcome is DeliveryOutcome.REJECTED

    @pytest.mark.parametrize(
        ("status", "body", "outcome"),
        [
            (200, {"id": "email-123"}, DeliveryOutcome.SENT),
            (200, {}, DeliveryOutcome.FAILED),
            (401, {"message": SECRET_MARKER}, DeliveryOutcome.REJECTED),
            (422, {"message": SECRET_MARKER}, DeliveryOutcome.REJECTED),
            (429, {"message": SECRET_MARKER}, DeliveryOutcome.FAILED),
            (503, {"message": SECRET_MARKER}, DeliveryOutcome.FAILED),
            (307, {}, DeliveryOutcome.REJECTED),
        ],
    )
    async def test_configured_email_delivery(
        self, status: int, body: dict[str, str], outcome: DeliveryOutcome
    ) -> None:
        settings = Settings(
            app_env=AppEnv.LOCAL,
            email_transport="provider_api",
            email_api_base_url="https://api.email.example.com",
            email_api_key=SECRET_MARKER,
            email_from_address="no-reply@example.com",
        )

        def respond(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert str(request.url) == "https://api.email.example.com/emails"
            assert request.headers["Authorization"] == f"Bearer {SECRET_MARKER}"
            assert request.headers["Idempotency-Key"] == "test-email-1"
            assert json.loads(request.content)["to"] == ["seller@example.com"]
            assert json.loads(request.content)["text"] == "Body"
            return httpx.Response(status, json=body)

        transport = ProviderApiEmailTransport(settings, transport=httpx.MockTransport(respond))
        assert transport.is_configured
        result = await transport.send(
            EmailMessage(
                to_address="seller@example.com",
                subject="Hi",
                text_body="Body",
                idempotency_key="test-email-1",
            )
        )
        assert result.outcome is outcome
        assert SECRET_MARKER not in (result.detail or "")

    async def test_provider_timeout_is_a_failed_delivery(self) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(SECRET_MARKER, request=request)

        settings = Settings(
            app_env=AppEnv.LOCAL,
            email_transport="provider_api",
            email_api_base_url="https://api.email.example.com",
            email_api_key=SECRET_MARKER,
            email_from_address="no-reply@example.com",
        )
        result = await ProviderApiEmailTransport(
            settings, transport=httpx.MockTransport(timeout)
        ).send(EmailMessage(to_address="seller@example.com", subject="Hi", text_body="Body"))
        assert result.outcome is DeliveryOutcome.FAILED
        assert SECRET_MARKER not in (result.detail or "")

    async def test_the_mock_transport_is_idempotent(self) -> None:
        transport = MockEmailTransport()
        message = EmailMessage(
            to_address="seller@example.com",
            subject="Reset your password",
            text_body="Link",
            idempotency_key="reset-1",
        )
        first = await transport.send(message)
        second = await transport.send(message)
        assert first.outcome is DeliveryOutcome.SENT
        assert second.outcome is DeliveryOutcome.DUPLICATE
        assert len(transport.sent) == 1

    def test_an_email_never_logs_its_body_or_its_recipient(self) -> None:
        message = EmailMessage(
            to_address="seller@example.com",
            subject="Reset your password",
            text_body="https://app.example.com/reset?token=SECRET-RESET-TOKEN",
        )
        redacted = message.redacted()
        assert "SECRET-RESET-TOKEN" not in str(redacted)
        assert redacted["to"] == "s****r@example.com"
        assert "seller@example.com" not in str(redacted)

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            ("disabled", "disabled"),
            ("console", "console"),
            ("mock", "mock"),
            ("provider_api", "provider_api"),
        ],
    )
    def test_the_builder_honours_the_setting(self, configured: str, expected: str) -> None:
        settings = Settings(app_env=AppEnv.LOCAL, email_transport=configured)
        assert build_email_transport(settings).name == expected

    async def test_the_console_transport_says_it_did_not_deliver(self) -> None:
        result = await ConsoleEmailTransport().send(
            EmailMessage(to_address="seller@example.com", subject="Hi", text_body="Body")
        )
        assert result.detail == "not delivered"


class TestEmailMasking:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("seller@example.com", "s****r@example.com"),
            ("ab@example.com", "**@example.com"),
            ("a@example.com", "*@example.com"),
            ("not-an-email", "not-an-email"),
            (None, None),
        ],
    )
    def test_the_domain_survives_and_the_local_part_does_not(
        self, value: str | None, expected: str | None
    ) -> None:
        assert mask_email(value) == expected
