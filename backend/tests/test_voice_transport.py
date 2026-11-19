"""V4 WP3.1: the VoiceTransport port, voice manifests and the platform voice account."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.models import AdminPermission, PlatformAdminRole, admin_permissions_for
from app.common.audit import AuditAction, AuditLog
from app.common.feature_flags import FeatureFlagService, FlagKey
from app.common.provider_health import (
    FAILURE_THRESHOLD,
    HealthState,
    ProviderHealth,
    ProviderHealthService,
)
from app.core.config import AppEnv, Settings
from app.core.errors import AppError, ErrorCode, ValidationError
from app.couriers.capabilities import CapabilityState
from app.db.session import system_session
from app.voice.manifest import (
    VOICE_MANIFEST_DIR,
    VoiceCapability,
    VoiceFact,
    available_voice_manifests,
    load_voice_manifests,
    mock_manifest,
    parse_voice_manifest,
)
from app.voice.models import VoiceProviderConnection
from app.voice.service import CALL_CAPABILITY, VoiceConnectionService, VoiceConnectionUpdate
from app.voice.transport import (
    MOCK_SIGNATURE_HEADER,
    DisabledVoiceTransport,
    DtmfAction,
    DtmfOption,
    MockVoiceTransport,
    SegmentKind,
    VoiceAvailabilityState,
    VoiceCall,
    VoiceCallOutcome,
    VoiceCallResult,
    VoiceEvent,
    VoiceEventKind,
    VoiceSegment,
    build_voice_transport,
)
from tests.test_admin import BOOTSTRAP_TOKEN, _provision_admin, admin_header
from tests.test_config import production_settings

PHONE = "+8801700000042"
CREDENTIAL = "voice-api-key-value-0001"
CALLBACK_SECRET = "voice-callback-secret-0001"
PROVIDERS = "/v1/admin/voice/providers"


def _call(**overrides: Any) -> VoiceCall:
    values: dict[str, Any] = {
        "to_e164": PHONE,
        "client_ref": f"confirmation:{uuid.uuid4()}:1",
        "audio_ref": "voice/intro-v1.wav",
    }
    values.update(overrides)
    return VoiceCall(**values)


# --------------------------------------------------------------------------- #
# The call
# --------------------------------------------------------------------------- #


class TestVoiceCall:
    @pytest.mark.parametrize(
        ("overrides", "match"),
        [
            ({"to_e164": "01700000042"}, "E.164"),
            ({"to_e164": "+0170000004"}, "E.164"),
            ({"client_ref": ""}, "client_ref"),
            ({"client_ref": "has space"}, "client_ref"),
            ({"max_retries": 1}, "max_retries"),
            ({"audio_ref": None}, "exactly one"),
            (
                {"segments": (VoiceSegment(SegmentKind.AUDIO, audio_ref="voice/a.wav"),)},
                "exactly one",
            ),
            ({"audio_ref": "bad ref with spaces"}, "audio_ref"),
            ({"dtmf_options": ()}, "DTMF"),
            (
                {
                    "dtmf_options": (
                        DtmfOption("1", DtmfAction.CONFIRM),
                        DtmfOption("1", DtmfAction.CANCEL),
                    )
                },
                "one thing",
            ),
        ],
    )
    def test_refusals_never_carry_the_number(self, overrides: dict[str, Any], match: str) -> None:
        with pytest.raises(ValueError, match=match) as caught:
            _call(**overrides)
        assert "1700000042" not in str(caught.value)

    def test_defaults_follow_the_script_and_we_retry_not_the_provider(self) -> None:
        call = _call()
        assert call.max_retries == 0
        assert [(o.digit, o.action) for o in call.dtmf_options] == [
            ("1", DtmfAction.CONFIRM),
            ("2", DtmfAction.CANCEL),
            ("3", DtmfAction.CALLBACK),
        ]

    def test_repr_and_redacted_mask_the_number(self) -> None:
        call = _call()
        assert "1700000042" not in repr(call)
        assert "1700000042" not in str(call.redacted())
        assert call.redacted()["client_ref"] == call.client_ref

    def test_segments_are_recordings_or_whole_numbers(self) -> None:
        call = _call(
            audio_ref=None,
            segments=(
                VoiceSegment(SegmentKind.AUDIO, audio_ref="voice/intro-v1.wav"),
                VoiceSegment(SegmentKind.NUMBER, number=1250),
            ),
        )
        assert len(call.segments) == 2
        for bad in (
            {"kind": SegmentKind.AUDIO},
            {"kind": SegmentKind.AUDIO, "audio_ref": "voice/a.wav", "number": 1},
            {"kind": SegmentKind.NUMBER, "number": -1},
            {"kind": SegmentKind.NUMBER, "number": 12.5},
            {"kind": SegmentKind.NUMBER, "number": True},
            {"kind": SegmentKind.NUMBER, "number": 5, "audio_ref": "voice/a.wav"},
        ):
            with pytest.raises(ValueError):
                VoiceSegment(**bad)

    def test_events_carry_a_digit_only_with_dtmf(self) -> None:
        assert VoiceEvent(VoiceEventKind.DTMF, "p-1", digit="1").digit == "1"
        assert VoiceEvent(VoiceEventKind.NO_ANSWER, "p-1").is_terminal
        assert not VoiceEvent(VoiceEventKind.ANSWERED, "p-1").is_terminal
        for bad in (
            {"kind": VoiceEventKind.DTMF, "provider_ref": "p-1"},
            {"kind": VoiceEventKind.ANSWERED, "provider_ref": "p-1", "digit": "1"},
            {"kind": VoiceEventKind.DTMF, "provider_ref": "p-1", "digit": "12"},
            {"kind": VoiceEventKind.ANSWERED, "provider_ref": ""},
            {"kind": VoiceEventKind.COMPLETED, "provider_ref": "p", "duration_seconds": -1},
        ):
            with pytest.raises(ValueError):
                VoiceEvent(**bad)

    def test_may_have_called(self) -> None:
        called = {
            outcome for outcome in VoiceCallOutcome if VoiceCallResult(outcome, "x").may_have_called
        }
        assert called == {
            VoiceCallOutcome.PLACED,
            VoiceCallOutcome.UNKNOWN,
            VoiceCallOutcome.DUPLICATE,
        }


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #


class TestDisabledTransport:
    async def test_places_nothing_and_refuses_every_callback(self) -> None:
        transport = DisabledVoiceTransport()
        availability = transport.availability()
        assert availability.state is VoiceAvailabilityState.NOT_CONFIGURED
        assert availability.detail == "VOICE_PROVIDER_CONTRACT_REQUIRED"
        assert not availability.can_place_calls

        result = await transport.place_call(_call())
        assert result.outcome is VoiceCallOutcome.NOT_CONFIGURED
        assert not result.may_have_called

        headers, body = MockVoiceTransport().callback(VoiceEvent(VoiceEventKind.ANSWERED, "p"))
        assert await transport.verify_callback(headers, body) is False
        assert transport.parse_callback(body) == []


class TestMockTransport:
    async def test_client_ref_is_the_idempotency_key(self) -> None:
        mock = MockVoiceTransport()
        call = _call()
        first = await mock.place_call(call)
        again = await mock.place_call(call)
        assert first.outcome is VoiceCallOutcome.PLACED and first.provider_ref
        assert again.outcome is VoiceCallOutcome.DUPLICATE
        assert again.provider_ref == first.provider_ref
        assert len(mock.calls) == 1

    async def test_scripted_outcomes(self) -> None:
        mock = MockVoiceTransport()
        mock.script(VoiceCallOutcome.FAILED, VoiceCallOutcome.UNKNOWN)
        failed_call, unknown_call = _call(), _call()

        failed = await mock.place_call(failed_call)
        assert failed.outcome is VoiceCallOutcome.FAILED
        # Certainly not placed: the same reference may be placed later.
        assert mock.provider_ref_for(failed_call.client_ref) is None

        unknown = await mock.place_call(unknown_call)
        assert unknown.outcome is VoiceCallOutcome.UNKNOWN
        # It may have rung: a repeat is a duplicate, never a second call.
        assert (await mock.place_call(unknown_call)).outcome is VoiceCallOutcome.DUPLICATE

        assert (await mock.place_call(failed_call)).outcome is VoiceCallOutcome.PLACED

    async def test_auth_failure_and_unavailable(self) -> None:
        mock = MockVoiceTransport()
        mock.script(VoiceCallOutcome.REJECTED, auth_failure=True)
        rejected = await mock.place_call(_call())
        assert rejected.outcome is VoiceCallOutcome.REJECTED and rejected.auth_failure

        down = MockVoiceTransport(available=False)
        assert down.availability().state is VoiceAvailabilityState.UNAVAILABLE
        assert (await down.place_call(_call())).outcome is VoiceCallOutcome.FAILED
        assert down.calls == []

    async def test_callbacks_are_verified_then_parsed(self) -> None:
        mock = MockVoiceTransport()
        at = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
        events = (
            VoiceEvent(VoiceEventKind.ANSWERED, "mock-call-1", client_ref="c:1", occurred_at=at),
            VoiceEvent(VoiceEventKind.DTMF, "mock-call-1", client_ref="c:1", digit="1"),
            VoiceEvent(VoiceEventKind.COMPLETED, "mock-call-1", duration_seconds=22),
        )
        headers, body = mock.callback(*events)
        assert await mock.verify_callback(headers, body)
        assert await mock.verify_callback(
            {MOCK_SIGNATURE_HEADER.upper(): headers[MOCK_SIGNATURE_HEADER]}, body
        )
        assert mock.parse_callback(body) == list(events)

        assert not await mock.verify_callback(headers, body + b" ")
        assert not await mock.verify_callback({}, body)
        forged_headers, forged = mock.callback(*events, secret="someone-else")
        assert not await mock.verify_callback(forged_headers, forged)

        with pytest.raises(ValueError):
            mock.parse_callback(b"not json")
        with pytest.raises(ValueError):
            mock.parse_callback(json.dumps({"events": [{"kind": "WHAT", "call_id": "x"}]}).encode())

    async def test_no_secret_refuses_everything(self) -> None:
        mock = MockVoiceTransport(callback_secret=None)
        headers, body = MockVoiceTransport().callback(VoiceEvent(VoiceEventKind.ANSWERED, "p"))
        empty_headers, empty_body = mock.callback(
            VoiceEvent(VoiceEventKind.ANSWERED, "p"), secret=""
        )
        assert not await mock.verify_callback(headers, body)
        assert not await mock.verify_callback(empty_headers, empty_body)


class TestSettingsAndFlag:
    def test_the_builder_defaults_to_disabled(self) -> None:
        assert isinstance(
            build_voice_transport(Settings(app_env=AppEnv.LOCAL)), DisabledVoiceTransport
        )
        mock = build_voice_transport(Settings(app_env=AppEnv.LOCAL, voice_transport="MOCK"))
        assert isinstance(mock, MockVoiceTransport)

    def test_unknown_transport_and_production_mock_are_refused(self) -> None:
        with pytest.raises(ValueError, match="VOICE_TRANSPORT must be one of"):
            Settings(app_env=AppEnv.LOCAL, voice_transport="twilio")
        with pytest.raises(ValueError, match="VOICE_TRANSPORT=mock is forbidden"):
            production_settings(voice_transport="mock")
        assert production_settings().voice_transport == "disabled"

    async def test_the_flag_defaults_off(self, db: AsyncSession) -> None:
        assert not await FeatureFlagService(db).is_enabled(FlagKey.VOICE_CONFIRMATION_ENABLED)

    def test_only_superadmin_writes_provider_credentials(self) -> None:
        for role in PlatformAdminRole:
            holds = AdminPermission.PROVIDER_CREDENTIALS_WRITE in admin_permissions_for(role)
            assert holds is (role is PlatformAdminRole.SUPERADMIN)


# --------------------------------------------------------------------------- #
# Manifests
# --------------------------------------------------------------------------- #

_PROOF = {"verified_at": "2026-11-19", "evidence": "CONTRACT.md §3"}


def _ready_raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "provider": "example",
        "display_name": "Example",
        "credential_fields": ["api_key", "account_id"],
        "capabilities": {
            "dtmf_capture": {"state": True, **_PROOF},
            "dynamic_audio": {"state": False, **_PROOF},
            "callback_signature": {"state": True, "detail": "hmac_sha256", **_PROOF},
            "caller_id": {"state": "unknown"},
        },
        "facts": {
            "max_call_seconds": {"value": 60, **_PROOF},
            "price_per_call_paisa": {"value": 150, **_PROOF},
        },
    }
    raw.update(overrides)
    return raw


class TestManifest:
    def test_a_complete_manifest_is_ready(self) -> None:
        manifest = parse_voice_manifest(_ready_raw())
        assert manifest.ready_for_calls and manifest.missing_for_calls == []
        assert manifest.supports(VoiceCapability.DTMF_CAPTURE)
        assert manifest.state(VoiceCapability.DYNAMIC_AUDIO) is CapabilityState.UNSUPPORTED
        assert manifest.state(VoiceCapability.CALLER_ID) is CapabilityState.UNKNOWN
        assert manifest.fact(VoiceFact.PRICE_PER_CALL_PAISA) == 150
        shape = manifest.as_dict()
        assert shape["capabilities"]["dtmf_capture"]["evidence"] == "CONTRACT.md §3"
        assert shape["facts"]["price_per_minute_paisa"]["value"] is None

    @pytest.mark.parametrize(
        "entry",
        [
            True,
            {"state": True},
            {"state": True, "verified_at": "2026-11-19"},
            {"state": True, "evidence": "CONTRACT.md"},
            {"state": True, "verified_at": "not-a-date", "evidence": "CONTRACT.md"},
            {"state": True, "verified_at": "2026-11-19", "evidence": "  "},
        ],
    )
    def test_a_claim_without_date_and_evidence_is_unknown(self, entry: Any) -> None:
        raw = _ready_raw()
        raw["capabilities"]["dtmf_capture"] = entry
        manifest = parse_voice_manifest(raw)
        assert manifest.state(VoiceCapability.DTMF_CAPTURE) is CapabilityState.UNKNOWN
        assert "DTMF_CAPTURE_UNVERIFIED" in manifest.missing_for_calls

    def test_a_signature_claim_names_its_scheme(self) -> None:
        raw = _ready_raw()
        raw["capabilities"]["callback_signature"] = {"state": True, **_PROOF}
        manifest = parse_voice_manifest(raw)
        assert manifest.state(VoiceCapability.CALLBACK_SIGNATURE) is CapabilityState.UNKNOWN
        assert "CALLBACK_SIGNATURE_UNVERIFIED" in manifest.missing_for_calls

    @pytest.mark.parametrize("value", [1.5, "150", True, -1, None])
    def test_a_price_is_whole_paisa_with_evidence(self, value: Any) -> None:
        raw = _ready_raw()
        raw["facts"]["price_per_call_paisa"] = {"value": value, **_PROOF}
        manifest = parse_voice_manifest(raw)
        assert manifest.fact(VoiceFact.PRICE_PER_CALL_PAISA) is None
        assert "PRICE_UNVERIFIED" in manifest.missing_for_calls

    def test_per_minute_price_counts_and_zero_call_length_does_not(self) -> None:
        raw = _ready_raw()
        raw["facts"] = {
            "max_call_seconds": {"value": 0, **_PROOF},
            "price_per_minute_paisa": {"value": 90, **_PROOF},
            "price_per_sms": {"value": 1, **_PROOF},
        }
        manifest = parse_voice_manifest(raw)
        assert manifest.missing_for_calls == ["MAX_CALL_SECONDS_UNVERIFIED"]
        unpriced = parse_voice_manifest(_ready_raw(facts={"max_call_seconds": {"value": 60}}))
        assert "MAX_CALL_SECONDS_UNVERIFIED" in unpriced.missing_for_calls

    def test_blockers_and_missing_fields_stop_calls(self) -> None:
        blocked = parse_voice_manifest(_ready_raw(blockers=["VOICE_LIVE_TEST_REQUIRED"]))
        assert blocked.missing_for_calls == ["VOICE_LIVE_TEST_REQUIRED"]
        no_fields = parse_voice_manifest(_ready_raw(credential_fields=[]))
        assert no_fields.missing_for_calls == ["CREDENTIAL_FIELDS_UNDECLARED"]
        with pytest.raises(ValueError):
            parse_voice_manifest(_ready_raw(credential_fields=["API KEY"]))
        with pytest.raises(ValueError):
            parse_voice_manifest(_ready_raw(credential_fields=["api_key", "api_key"]))
        with pytest.raises(ValueError):
            parse_voice_manifest(_ready_raw(provider="Bad Name"))

    def test_unknown_capabilities_are_skipped(self) -> None:
        raw = _ready_raw()
        raw["capabilities"]["record_call"] = {"state": True, **_PROOF}
        manifest = parse_voice_manifest(raw)
        assert set(manifest.capabilities) == set(VoiceCapability)

    def test_the_loader_skips_a_broken_file(self, tmp_path: Path) -> None:
        import yaml

        (tmp_path / "example.yaml").write_text(yaml.safe_dump(_ready_raw()), encoding="utf-8")
        (tmp_path / "broken.yaml").write_text("provider: [unclosed", encoding="utf-8")
        (tmp_path / "bad_slug.yaml").write_text(
            yaml.safe_dump(_ready_raw(provider="Bad")), encoding="utf-8"
        )
        loaded = load_voice_manifests(directory=tmp_path)
        assert list(loaded) == ["example"] and loaded["example"].ready_for_calls
        assert load_voice_manifests(directory=tmp_path / "missing") == {}

    def test_shipped_manifests_never_include_the_mock(self) -> None:
        shipped = load_voice_manifests(directory=VOICE_MANIFEST_DIR)
        assert "mock" not in shipped
        local = available_voice_manifests(Settings(app_env=AppEnv.LOCAL))
        assert "mock" not in local
        with_mock = available_voice_manifests(
            Settings(app_env=AppEnv.LOCAL, voice_transport="mock")
        )
        assert with_mock["mock"].ready_for_calls
        assert mock_manifest().credential_fields == ("api_key",)


# --------------------------------------------------------------------------- #
# The platform account
# --------------------------------------------------------------------------- #


def _service(db: AsyncSession, settings: Any, **manifests: Any) -> VoiceConnectionService:
    from app.api.deps import get_vault

    return VoiceConnectionService(db, get_vault(settings), manifests or {"mock": mock_manifest()})


class TestConnectionService:
    async def test_unknown_provider_is_refused(
        self, system_db: AsyncSession, settings: Any
    ) -> None:
        with pytest.raises(AppError) as caught:
            await _service(system_db, settings).update(
                "twilio", VoiceConnectionUpdate(credentials={"api_key": "x"}), actor="t"
            )
        assert caught.value.code is ErrorCode.VOICE_PROVIDER_UNKNOWN

    async def test_credentials_must_be_exactly_the_manifest_fields(
        self, system_db: AsyncSession, settings: Any
    ) -> None:
        service = _service(system_db, settings)
        for bad in ({}, {"api_key": CREDENTIAL, "extra": "x"}, {"token": "x"}, {"api_key": " "}):
            with pytest.raises(ValidationError):
                await service.update("mock", VoiceConnectionUpdate(credentials=bad), actor="t")
        with pytest.raises(ValidationError):
            await service.update("mock", VoiceConnectionUpdate(), actor="t")

    async def test_secrets_are_encrypted_bound_to_the_row_and_audited_without_values(
        self, system_db: AsyncSession, settings: Any
    ) -> None:
        service = _service(system_db, settings)
        row = await service.update(
            "mock",
            VoiceConnectionUpdate(
                credentials={"api_key": CREDENTIAL},
                callback_secret=CALLBACK_SECRET,
                reason="first connection",
            ),
            actor="test-admin",
        )
        assert row.enabled is False
        assert row.credentials_encrypted and CREDENTIAL not in row.credentials_encrypted
        assert (
            row.callback_secret_encrypted and CALLBACK_SECRET not in row.callback_secret_encrypted
        )
        assert row.masked_identifier == "****0001"
        assert service.credentials(row) == {"api_key": CREDENTIAL}
        assert service.callback_secret(row) == CALLBACK_SECRET

        # The envelope is bound to this row: copied elsewhere it does not open.
        from app.api.deps import get_vault

        with pytest.raises(Exception):  # noqa: B017 - AESGCM InvalidTag
            get_vault(settings).decrypt(
                row.credentials_encrypted, context=f"voice_provider_connection:{uuid.uuid4()}"
            )

        await system_db.flush()  # record_audit only adds; autoflush is off
        audit = (
            (
                await system_db.execute(
                    sa.select(AuditLog).where(
                        AuditLog.action == str(AuditAction.ADMIN_VOICE_PROVIDER_UPDATED),
                        AuditLog.entity_id == str(row.id),
                    )
                )
            )
            .scalars()
            .one()
        )
        dumped = json.dumps(audit.context, default=str)
        assert CREDENTIAL not in dumped and CALLBACK_SECRET not in dumped
        assert audit.context["created"] is True and audit.context["changed"] == [
            "credentials",
            "callback_secret",
        ]

        # Rotating only the secret keeps the credentials.
        same = await service.update(
            "mock", VoiceConnectionUpdate(callback_secret="another-secret-0002"), actor="t"
        )
        assert same.id == row.id and service.credentials(same) == {"api_key": CREDENTIAL}

    async def test_turning_on_needs_a_ready_manifest_credentials_and_a_callback_secret(
        self, system_db: AsyncSession, settings: Any
    ) -> None:
        service = _service(system_db, settings)
        with pytest.raises(AppError) as caught:
            await service.update(
                "mock",
                VoiceConnectionUpdate(credentials={"api_key": CREDENTIAL}, enabled=True),
                actor="t",
            )
        assert caught.value.code is ErrorCode.VOICE_PROVIDER_NOT_READY
        assert caught.value.details["missing"] == ["CALLBACK_SECRET_MISSING"]

        row = await service.update(
            "mock",
            VoiceConnectionUpdate(
                credentials={"api_key": CREDENTIAL}, callback_secret=CALLBACK_SECRET, enabled=True
            ),
            actor="t",
        )
        assert row.enabled is True

        unready = parse_voice_manifest(_ready_raw(provider="example", facts={}))
        other = _service(system_db, settings, example=unready)
        with pytest.raises(AppError) as caught:
            await other.update(
                "example",
                VoiceConnectionUpdate(
                    credentials={"api_key": "k" * 10, "account_id": "a" * 10},
                    callback_secret=CALLBACK_SECRET,
                    enabled=True,
                ),
                actor="t",
            )
        assert caught.value.details["missing"] == [
            "MAX_CALL_SECONDS_UNVERIFIED",
            "PRICE_UNVERIFIED",
        ]

    async def test_caller_id_needs_the_verified_capability(
        self, system_db: AsyncSession, settings: Any
    ) -> None:
        with pytest.raises(ValidationError):
            await _service(system_db, settings).update(
                "mock",
                VoiceConnectionUpdate(
                    credentials={"api_key": CREDENTIAL}, caller_id="+8809600000000"
                ),
                actor="t",
            )
        raw = _ready_raw()
        raw["capabilities"]["caller_id"] = {"state": True, **_PROOF}
        service = _service(system_db, settings, example=parse_voice_manifest(raw))
        creds = {"api_key": "k" * 10, "account_id": "a" * 10}
        with pytest.raises(ValidationError):
            await service.update(
                "example",
                VoiceConnectionUpdate(credentials=creds, caller_id="09600000000"),
                actor="t",
            )
        row = await service.update(
            "example",
            VoiceConnectionUpdate(credentials=creds, caller_id="+8809600000000"),
            actor="t",
        )
        assert row.caller_id == "+8809600000000"

    async def test_health_and_breaker(self, system_db: AsyncSession, settings: Any) -> None:
        service = _service(system_db, settings)

        async def state() -> str | None:
            row = (
                await system_db.execute(
                    sa.select(ProviderHealth).where(
                        ProviderHealth.provider == "mock",
                        ProviderHealth.capability == CALL_CAPABILITY,
                    )
                )
            ).scalar_one_or_none()
            return row.state if row else None

        # A refused number, a duplicate, "not configured": not the provider's health.
        for outcome in (
            VoiceCallOutcome.REJECTED,
            VoiceCallOutcome.DUPLICATE,
            VoiceCallOutcome.NOT_CONFIGURED,
        ):
            await service.record_result("mock", VoiceCallResult(outcome, "mock"))
        assert await state() is None

        await service.record_result("mock", VoiceCallResult(VoiceCallOutcome.PLACED, "mock"))
        assert await state() == HealthState.HEALTHY
        assert await service.breaker_allows("mock")

        for _ in range(FAILURE_THRESHOLD):
            await service.record_result(
                "mock", VoiceCallResult(VoiceCallOutcome.UNKNOWN, "mock", detail="TIMEOUT")
            )
        assert await state() == HealthState.DOWN
        assert not await service.breaker_allows("mock")

        await ProviderHealthService(system_db).reset("mock", capability=CALL_CAPABILITY)
        await service.record_result(
            "mock", VoiceCallResult(VoiceCallOutcome.REJECTED, "mock", auth_failure=True)
        )
        assert await state() == HealthState.NEEDS_RECONNECT


# --------------------------------------------------------------------------- #
# Admin console
# --------------------------------------------------------------------------- #


@pytest.fixture
def voice_admin(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A bootstrap SUPERADMIN token, with ``VOICE_TRANSPORT`` as each test sets it."""
    from app.core.config import get_settings, reset_settings_cache

    monkeypatch.setenv("ADMIN_API_TOKENS", json.dumps([BOOTSTRAP_TOKEN]))

    def configure(transport: str) -> Any:
        monkeypatch.setenv("VOICE_TRANSPORT", transport)
        reset_settings_cache()
        return get_settings()

    configure("mock")
    try:
        yield configure
    finally:
        monkeypatch.undo()
        reset_settings_cache()


@pytest_asyncio.fixture
async def clean_voice() -> AsyncIterator[None]:
    yield
    async with system_session("test: clean voice") as db:
        await db.execute(sa.delete(VoiceProviderConnection))
        await db.execute(sa.delete(ProviderHealth).where(ProviderHealth.kind == "VOICE"))


class TestAdminRoutes:
    async def test_nothing_is_listed_without_a_contract(
        self, client: AsyncClient, voice_admin: Any
    ) -> None:
        voice_admin("disabled")
        response = await client.get(PROVIDERS, headers=admin_header())
        assert response.status_code == 200 and response.json() == []
        refused = await client.put(
            f"{PROVIDERS}/mock",
            headers=admin_header(),
            json={"credentials": {"api_key": CREDENTIAL}, "reason": "try the mock"},
        )
        assert refused.status_code == 404 and refused.json()["code"] == "VOICE_PROVIDER_UNKNOWN"

    async def test_connect_and_list_never_return_a_secret(
        self, client: AsyncClient, voice_admin: Any, clean_voice: None
    ) -> None:
        listed = (await client.get(PROVIDERS, headers=admin_header())).json()
        assert [item["provider"] for item in listed] == ["mock"]
        assert listed[0]["connection"] is None and listed[0]["manifest"]["ready_for_calls"]

        response = await client.put(
            f"{PROVIDERS}/mock",
            headers=admin_header(),
            json={
                "credentials": {"api_key": CREDENTIAL},
                "callback_secret": CALLBACK_SECRET,
                "enabled": True,
                "reason": "staging mock check",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["connection"]["enabled"] is True
        assert body["connection"]["masked_identifier"] == "****0001"
        for text in (response.text, (await client.get(PROVIDERS, headers=admin_header())).text):
            assert CREDENTIAL not in text and CALLBACK_SECRET not in text
            assert "credentials_encrypted" not in text and "key_version" not in text

        short = await client.put(
            f"{PROVIDERS}/mock",
            headers=admin_header(),
            json={"callback_secret": "short", "reason": "rotate the secret"},
        )
        assert short.status_code == 422

    async def test_ops_reads_but_cannot_set_credentials(
        self, client: AsyncClient, voice_admin: Any, clean_voice: None
    ) -> None:
        token = f"INSECURE_DEV_ops_voice_token_for_tests_only_{uuid.uuid4().hex}"
        await _provision_admin(PlatformAdminRole.OPS, token, voice_admin("mock"))
        assert (await client.get(PROVIDERS, headers=admin_header(token))).status_code == 200
        refused = await client.put(
            f"{PROVIDERS}/mock",
            headers=admin_header(token),
            json={"credentials": {"api_key": CREDENTIAL}, "reason": "ops tries to connect"},
        )
        assert refused.status_code == 403
