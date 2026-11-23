"""Shop intro audio for confirmation calls (V4 WP3.3).

The Bangla script, the file checks, the TTS port (disabled until a contract)
and its fake, the operator's upload, the seller's listen → turn on → turn off,
a rename retiring the old name's recording, the provider upload in the call
worker, and the spoken amount only with a verified ``dynamic_audio``.
"""

from __future__ import annotations

import io
import json
import uuid
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.admin.models import PlatformAdminRole
from app.common.audit import AuditLog
from app.common.outbox import OutboxEvent, OutboxTopic
from app.confirmations import calls, jobs
from app.core.config import AppEnv, Settings
from app.core.errors import AppError
from app.db.session import session_scope, system_session
from app.voice import intro
from app.voice.intro import (
    MAX_AUDIO_BYTES,
    amount_prompts,
    call_audio,
    inspect_audio,
    intro_script,
)
from app.voice.manifest import mock_manifest, parse_voice_manifest
from app.voice.models import IntroState, VoiceIntroAudio, VoiceProviderConnection
from app.voice.transport import (
    DisabledVoiceTransport,
    MockVoiceTransport,
    SegmentKind,
    VoiceAudio,
    VoiceAudioOutcome,
    VoiceAudioResult,
)
from app.voice.tts import (
    TTS_PROVIDER_REQUIRED,
    DisabledTtsClient,
    FakeTtsClient,
    TtsAvailability,
    TtsError,
    build_tts_client,
    silent_wav,
)
from tests.test_admin import BOOTSTRAP_TOKEN, _provision_admin, admin_client, admin_header
from tests.test_auth_flow import auth_header
from tests.test_config import production_settings
from tests.test_voice_calls import (
    NOON,
    Clock,
    _confirmation,
    _queued,
    _reset_voice,
    _shop,
    _Tenant,
    engine_on,
)

__all__ = ["admin_client", "engine_on"]  # fixtures, imported for pytest

SCRIPT_TAIL = (
    "থেকে একটি অর্ডার করেছেন। অর্ডার কনফার্ম করতে ১ চাপুন। বাতিল করতে ২ চাপুন। দোকানের সাথে কথা বলতে ৩ চাপুন।"
)


def _wav(seconds: float = 0.5) -> bytes:
    return silent_wav(milliseconds=int(seconds * 1000))


def _reason(exc: pytest.ExceptionInfo[AppError]) -> str:
    return str((exc.value.details or {}).get("reason"))


# ----------------------------------------------------------------- script ---


def test_the_script_is_the_spec_text_with_the_shop_name() -> None:
    assert intro_script("  Rupa   Fashion ") == f"আসসালামু আলাইকুম। আপনি Rupa Fashion {SCRIPT_TAIL}"
    assert intro_script("রূপা ফ্যাশন").startswith("আসসালামু আলাইকুম। আপনি রূপা ফ্যাশন থেকে")


# ------------------------------------------------------------------ files ---


class TestInspectAudio:
    def test_a_wav_is_measured(self) -> None:
        info = inspect_audio(_wav(0.5))
        assert (info.content_type, info.extension, info.duration_ms) == ("audio/wav", "wav", 500)

    def test_an_mp3_is_told_by_its_bytes(self) -> None:
        assert inspect_audio(b"ID3\x04\x00" + b"\x00" * 64).content_type == "audio/mpeg"
        frame = inspect_audio(bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 64)
        assert (frame.extension, frame.duration_ms) == ("mp3", None)

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            (b"", "EMPTY"),
            (b"ID3" + b"\x00" * MAX_AUDIO_BYTES, "TOO_LARGE"),
            (b"just some text, not audio", "NOT_WAV_OR_MP3"),
            (b"RIFF\x00\x00\x00\x00WAVEgarbage", "UNREADABLE_WAV"),
            (silent_wav(milliseconds=61_000, rate=800), "TOO_LONG"),
            (silent_wav(milliseconds=0), "EMPTY"),
        ],
    )
    def test_anything_else_is_refused(self, content: bytes, reason: str) -> None:
        with pytest.raises(AppError) as caught:
            inspect_audio(content)
        assert caught.value.code == "VOICE_AUDIO_INVALID" and _reason(caught) == reason

    def test_a_non_pcm_wav_is_unreadable(self) -> None:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\x00\x00" * 800)
        raw = bytearray(buffer.getvalue())
        raw[20:22] = (3).to_bytes(2, "little")  # IEEE float, not PCM
        with pytest.raises(AppError) as caught:
            inspect_audio(bytes(raw))
        assert _reason(caught) == "UNREADABLE_WAV"


# ------------------------------------------------------------- the amount ---


class TestCallAudio:
    def test_without_prompts_only_the_intro_plays(self) -> None:
        assert call_audio("voice/x/intro-v1.wav", cod_paisa=125_000, prompts=None) == {
            "audio_ref": "voice/x/intro-v1.wav"
        }

    def test_the_amount_is_appended_in_whole_taka(self) -> None:
        audio = call_audio("intro", cod_paisa=125_000, prompts=("pre", "post"))
        kinds = [(s.kind, s.audio_ref, s.number) for s in audio["segments"]]
        assert kinds == [
            (SegmentKind.AUDIO, "intro", None),
            (SegmentKind.AUDIO, "pre", None),
            (SegmentKind.NUMBER, None, 1250),
            (SegmentKind.AUDIO, "post", None),
        ]

    @pytest.mark.parametrize("cod", [0, 125_050])
    def test_no_amount_or_a_part_taka_is_never_spoken(self, cod: int) -> None:
        assert call_audio("intro", cod_paisa=cod, prompts=("pre", "post")) == {"audio_ref": "intro"}

    def test_prompts_need_verified_dynamic_audio_and_both_refs(self) -> None:
        both = Settings(
            app_env=AppEnv.TEST,
            voice_amount_prefix_audio_ref="voice/platform/amount-pre.wav",
            voice_amount_suffix_audio_ref="voice/platform/amount-post.wav",
        )
        assert amount_prompts(mock_manifest(), both) == (
            "voice/platform/amount-pre.wav",
            "voice/platform/amount-post.wav",
        )
        assert amount_prompts(None, both) is None
        one_file = parse_voice_manifest(
            {
                "provider": "example",
                "display_name": "Example",
                "capabilities": {
                    "dynamic_audio": {
                        "state": False,
                        "verified_at": "2026-11-20",
                        "evidence": "CONTRACT.md §2",
                    }
                },
            }
        )
        assert amount_prompts(one_file, both) is None
        with pytest.raises(ValueError, match="go together"):
            Settings(app_env=AppEnv.TEST, voice_amount_prefix_audio_ref="voice/pre.wav")


def test_config_refuses_a_fake_tts_when_deployed_and_bad_refs() -> None:
    with pytest.raises(ValueError, match="TTS_PROVIDER=fake is forbidden"):
        production_settings(tts_provider="fake")
    assert production_settings().tts_provider == "disabled"
    with pytest.raises(ValueError):
        Settings(app_env=AppEnv.TEST, tts_provider="someone")
    with pytest.raises(ValueError):
        Settings(app_env=AppEnv.TEST, voice_amount_prefix_audio_ref="has space?")


# ------------------------------------------------------- port + manifest ---


class TestPort:
    def test_audio_delivery_must_be_known_for_calls(self) -> None:
        raw: dict[str, Any] = {
            "provider": "example",
            "display_name": "Example",
            "credential_fields": ["api_key"],
            "capabilities": {
                name: {"state": True, "verified_at": "2026-11-20", "evidence": "CONTRACT.md"}
                for name in ("dtmf_capture", "callback_signature")
            },
            "facts": {
                "max_call_seconds": {"value": 60, "verified_at": "2026-11-20", "evidence": "x"},
                "price_per_call_paisa": {
                    "value": 150,
                    "verified_at": "2026-11-20",
                    "evidence": "x",
                },
            },
        }
        raw["capabilities"]["callback_signature"]["detail"] = "hmac_sha256"
        assert parse_voice_manifest(raw).missing_for_calls == ["AUDIO_DELIVERY_UNVERIFIED"]
        raw["capabilities"]["audio_upload"] = {
            "state": False,
            "verified_at": "2026-11-20",
            "evidence": "CONTRACT.md: fetches a URL",
        }
        assert parse_voice_manifest(raw).ready_for_calls

    async def test_the_disabled_transport_uploads_nothing(self) -> None:
        result = await DisabledVoiceTransport().upload_audio(
            VoiceAudio("voice/t/intro-v1.wav", _wav(), "audio/wav")
        )
        assert result.outcome is VoiceAudioOutcome.NOT_CONFIGURED and result.audio_ref is None

    async def test_the_mock_keeps_one_ref_per_key_and_can_fail(self) -> None:
        mock = MockVoiceTransport()
        audio = VoiceAudio("voice/t/intro-v1.wav", _wav(), "audio/wav")
        mock.script_audio(VoiceAudioOutcome.FAILED)
        assert (await mock.upload_audio(audio)).outcome is VoiceAudioOutcome.FAILED
        first = await mock.upload_audio(audio)
        again = await mock.upload_audio(audio)
        assert first.outcome is VoiceAudioOutcome.UPLOADED and first.audio_ref == again.audio_ref
        assert len(mock.uploads) == 1
        assert "bytes" in repr(audio) and "RIFF" not in repr(audio)

    def test_audio_and_results_refuse_bad_shapes(self) -> None:
        with pytest.raises(ValueError):
            VoiceAudio("voice/t/intro-v1.wav", _wav(), "text/plain")
        with pytest.raises(ValueError):
            VoiceAudio("voice/t/intro-v1.wav", b"", "audio/wav")
        with pytest.raises(ValueError):
            VoiceAudio("bad key?", _wav(), "audio/wav")
        with pytest.raises(ValueError):
            VoiceAudioResult(VoiceAudioOutcome.UPLOADED, "mock")
        with pytest.raises(ValueError):
            VoiceAudioResult(VoiceAudioOutcome.FAILED, "mock", audio_ref="ref")


class TestTts:
    async def test_without_a_contract_tts_makes_nothing(self) -> None:
        client = build_tts_client(Settings(app_env=AppEnv.TEST))
        assert isinstance(client, DisabledTtsClient)
        assert client.availability() is TtsAvailability.NOT_CONFIGURED
        with pytest.raises(TtsError) as caught:
            await client.synthesize("x", language="bn-BD")
        assert caught.value.code == TTS_PROVIDER_REQUIRED and not caught.value.retryable

    async def test_the_fake_is_local_only_and_records_requests(self) -> None:
        fake = build_tts_client(Settings(app_env=AppEnv.TEST, tts_provider="fake"))
        assert isinstance(fake, FakeTtsClient)
        audio = await fake.synthesize("হ্যালো", language="bn-BD")
        assert inspect_audio(audio.content).content_type == "audio/wav"
        assert fake.requests == [("হ্যালো", "bn-BD")]


# --------------------------------------------------------------- fixtures ---


@dataclass
class Store:
    files: dict[str, tuple[bytes, str]]
    gets: int = 0

    async def put(self, key: str, content: bytes, content_type: str = "") -> None:
        self.files[key] = (content, content_type)

    async def get(self, key: str) -> bytes:
        self.gets += 1
        return self.files[key][0]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    found = Store({})
    monkeypatch.setattr(intro, "audio_storage", lambda: found)
    return found


@pytest.fixture
def tts(monkeypatch: pytest.MonkeyPatch) -> FakeTtsClient:
    fake = FakeTtsClient()
    monkeypatch.setattr(intro, "tts_client", lambda: fake)
    return fake


@pytest.fixture
async def live_voice(
    engine_on: None, admin_client: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[MockVoiceTransport]:
    """The real gate on the mock provider (its manifest verifies ``audio_upload``)."""
    from app.core.config import reset_settings_cache

    monkeypatch.setenv("VOICE_TRANSPORT", "mock")
    reset_settings_cache()
    mock = MockVoiceTransport()
    monkeypatch.setattr(calls, "voice_transport", lambda: mock)
    clock = Clock(NOON)
    monkeypatch.setattr(jobs, "utc_now", clock)
    monkeypatch.setattr(calls, "utc_now", clock)
    await _reset_voice()
    async with system_session("test: mock voice account") as db:
        db.add(
            VoiceProviderConnection(
                provider="mock", enabled=True, created_by="test", updated_by="test", config={}
            )
        )
    yield mock
    await _reset_voice()
    monkeypatch.delenv("VOICE_TRANSPORT")
    reset_settings_cache()


async def _upload(
    client: AsyncClient, tenant_id: Any, content: bytes | None = None, token: str = BOOTSTRAP_TOKEN
) -> Any:
    return await client.put(
        f"/v1/admin/voice/intros/{tenant_id}",
        files={"file": ("intro.wav", content if content is not None else _wav(), "audio/wav")},
        headers=admin_header(token),
    )


async def _intros(shop: dict[str, Any]) -> list[VoiceIntroAudio]:
    with _Tenant(shop):
        async with session_scope() as db:
            rows = (await db.scalars(sa.select(VoiceIntroAudio))).all()
    return sorted(rows, key=lambda row: row.version)


async def _status(client: AsyncClient, shop: dict[str, Any]) -> dict[str, Any]:
    response = await client.get("/v1/voice/intro", headers=auth_header(shop))
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _listen_and_turn_on(client: AsyncClient, shop: dict[str, Any], version: int) -> Any:
    played = await client.get(f"/v1/voice/intro/{version}/audio", headers=auth_header(shop))
    assert played.status_code == 200, played.text
    return await client.post(f"/v1/voice/intro/{version}/turn-on", headers=auth_header(shop))


async def _requests(tenant_id: Any) -> list[OutboxEvent]:
    async with system_session("test: intro requests") as db:
        return list(
            (
                await db.scalars(
                    sa.select(OutboxEvent).where(
                        OutboxEvent.topic == str(OutboxTopic.VOICE_INTRO_REQUESTED),
                        OutboxEvent.tenant_id == uuid.UUID(str(tenant_id)),
                    )
                )
            ).all()
        )


async def _handle(shop: dict[str, Any], event: OutboxEvent) -> None:
    import app.voice.jobs  # noqa: F401  (registers the handler, as the worker does)
    from app.worker.jobs import HANDLERS

    with _Tenant(shop):
        async with session_scope() as db:
            await HANDLERS[event.topic](db, event)


# ---------------------------------------------------------------- the API ---


def test_the_intro_topic_has_a_handler() -> None:
    import app.voice.jobs  # noqa: F401
    from app.worker.jobs import HANDLERS

    assert str(OutboxTopic.VOICE_INTRO_REQUESTED) in HANDLERS


async def test_with_voice_off_the_card_is_hidden(client: AsyncClient, live_voice: Any) -> None:
    from tests.conftest_commerce import signed_in_shop

    shop = await signed_in_shop(client, "01700000004")
    response = await client.get("/v1/voice/intro", headers=auth_header(shop))
    assert response.status_code != 200 and response.json()["code"] == "FEATURE_DISABLED"


async def test_the_operator_uploads_and_the_seller_listens_before_turning_on(
    client: AsyncClient, live_voice: MockVoiceTransport, store: Store, settings: Any
) -> None:
    shop = await _shop(client, "01700000005")
    tenant = shop["tenant_id"]

    first = await _status(client, shop)
    assert first["status"] == "NONE" and first["live"] is None and first["latest"] is None
    assert first["script"] == f"আসসালামু আলাইকুম। আপনি Test Shop {SCRIPT_TAIL}"
    assert first["tts_available"] is False
    assert (first["calls_on"], first["calls_off_reason"]) == (False, "NO_INTRO_AUDIO")

    # Who may upload: OPS yes, SUPPORT no; only WAV/MP3; only a real shop.
    await _provision_admin(PlatformAdminRole.SUPPORT, "support-" + "s" * 40, settings)
    await _provision_admin(PlatformAdminRole.OPS, "ops-" + "o" * 44, settings)
    denied = await _upload(client, tenant, token="support-" + "s" * 40)
    assert denied.status_code == 403
    bad = await _upload(client, tenant, content=b"not audio at all")
    assert bad.status_code == 422 and bad.json()["code"] == "VOICE_AUDIO_INVALID"
    assert (await _upload(client, uuid.uuid4())).status_code == 404

    uploaded = await _upload(client, tenant, token="ops-" + "o" * 44)
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["script"] == first["script"]
    (version,) = body["versions"]
    assert (version["version"], version["state"], version["source"]) == (1, "READY", "UPLOAD")
    assert version["uploaded_to"] == [] and version["duration_ms"] == 500
    key = f"voice/{tenant}/intro-v1.wav"
    assert store.files[key] == (_wav(), "audio/wav")
    async with system_session("test: audit") as db:
        audit = await db.scalar(
            sa.select(AuditLog).where(
                AuditLog.action == "admin.voice_intro_uploaded",
                AuditLog.tenant_id == uuid.UUID(tenant),
            )
        )
    assert audit is not None and "RIFF" not in json.dumps(audit.context, default=str)

    ready = await _status(client, shop)
    assert ready["status"] == "READY" and ready["latest"]["previewed"] is False

    refused = await client.post("/v1/voice/intro/1/turn-on", headers=auth_header(shop))
    assert refused.status_code == 409 and refused.json()["code"] == "VOICE_INTRO_NOT_PREVIEWED"

    played = await client.get("/v1/voice/intro/1/audio", headers=auth_header(shop))
    assert played.status_code == 200 and played.content == _wav()
    assert played.headers["content-type"] == "audio/wav"
    assert played.headers["cache-control"] == "no-store"

    on = await client.post("/v1/voice/intro/1/turn-on", headers=auth_header(shop))
    assert on.status_code == 200, on.text
    assert on.json()["status"] == "ON" and on.json()["live"]["version"] == 1
    # On, but the mock provider needs the file first: the call worker does that.
    assert on.json()["calls_off_reason"] == "NO_INTRO_AUDIO"

    # Another shop cannot hear or switch this one's recording.
    other = await _shop(client, "01700000006")
    assert (
        await client.get("/v1/voice/intro/1/audio", headers=auth_header(other))
    ).status_code == 404
    assert (
        await client.post("/v1/voice/intro/1/turn-on", headers=auth_header(other))
    ).status_code == 404

    listed = await client.get(f"/v1/admin/voice/intros/{tenant}", headers=admin_header())
    assert listed.status_code == 200 and listed.json()["versions"][0]["state"] == "ON"


async def test_a_newer_recording_replaces_the_one_on_only_when_turned_on(
    client: AsyncClient, live_voice: MockVoiceTransport, store: Store
) -> None:
    shop = await _shop(client, "01700000007")
    tenant = shop["tenant_id"]
    assert (await _upload(client, tenant)).status_code == 200
    assert (await _listen_and_turn_on(client, shop, 1)).status_code == 200
    assert (await _upload(client, tenant, content=_wav(0.7))).status_code == 200
    assert (await _upload(client, tenant, content=_wav(0.9))).status_code == 200
    v1, v2, v3 = await _intros(shop)
    # v1 still plays; v2 was replaced by the newer v3 before anyone heard it.
    assert (v1.state, v2.state, v2.retired_reason, v3.state) == (
        "ON",
        "RETIRED",
        "REPLACED",
        "READY",
    )
    status = await _status(client, shop)
    assert status["live"]["version"] == 1 and status["latest"]["version"] == 3
    assert (
        await client.get("/v1/voice/intro/2/audio", headers=auth_header(shop))
    ).status_code == 404

    assert (await _listen_and_turn_on(client, shop, 3)).status_code == 200
    v1, _, v3 = await _intros(shop)
    assert (v1.state, v1.retired_reason, v3.state) == ("RETIRED", "REPLACED", "ON")

    off = await client.post("/v1/voice/intro/turn-off", headers=auth_header(shop))
    assert off.status_code == 200 and off.json()["status"] == "READY" and off.json()["live"] is None
    assert (
        await client.post("/v1/voice/intro/turn-off", headers=auth_header(shop))
    ).status_code == 200
    # Already heard: it can go back on without another listen.
    back = await client.post("/v1/voice/intro/3/turn-on", headers=auth_header(shop))
    assert back.status_code == 200 and back.json()["status"] == "ON"


async def test_a_rename_retires_the_old_name_and_tts_makes_the_new_one_once(
    client: AsyncClient, live_voice: MockVoiceTransport, store: Store, tts: FakeTtsClient
) -> None:
    shop = await _shop(client, "01700000009")
    tenant = shop["tenant_id"]
    assert (await _upload(client, tenant)).status_code == 200
    assert (await _listen_and_turn_on(client, shop, 1)).status_code == 200

    renamed = await client.patch(
        "/v1/tenant", json={"name": "Rupa Fashion"}, headers=auth_header(shop)
    )
    assert renamed.status_code == 200, renamed.text
    (old,) = await _intros(shop)
    assert (old.state, old.retired_reason) == ("RETIRED", "SHOP_RENAMED")
    status = await _status(client, shop)
    assert status["status"] == "NONE" and status["shop_name"] == "Rupa Fashion"
    assert status["script"] == f"আসসালামু আলাইকুম। আপনি Rupa Fashion {SCRIPT_TAIL}"
    assert status["calls_on"] is False
    gone = await client.post("/v1/voice/intro/1/turn-on", headers=auth_header(shop))
    assert gone.status_code == 404

    (event,) = await _requests(tenant)
    await _handle(shop, event)
    await _handle(shop, event)  # a retried event makes nothing more
    _, made = await _intros(shop)
    assert (made.version, made.state, made.source, made.shop_name) == (
        2,
        "READY",
        "TTS",
        "Rupa Fashion",
    )
    assert made.created_by == "tts:fake" and made.storage_key == f"voice/{tenant}/intro-v2.wav"
    assert tts.requests == [(status["script"], "bn-BD")]

    # Asking again for a name already recorded queues nothing new.
    again = await client.post("/v1/voice/intro/request", headers=auth_header(shop))
    assert again.status_code == 202 and len(await _requests(tenant)) == 1


async def test_the_request_needs_a_tts_provider_and_a_retryable_failure_retries(
    client: AsyncClient, live_voice: MockVoiceTransport, store: Store, tts: FakeTtsClient
) -> None:
    shop = await _shop(client, "01700000010")
    tts.available = False
    refused = await client.post("/v1/voice/intro/request", headers=auth_header(shop))
    assert refused.status_code == 409 and refused.json()["code"] == "TTS_PROVIDER_REQUIRED"
    assert await _requests(shop["tenant_id"]) == []

    tts.available = True
    queued = await client.post("/v1/voice/intro/request", headers=auth_header(shop))
    assert queued.status_code == 202 and queued.json()["tts_available"] is True
    (event,) = await _requests(shop["tenant_id"])
    tts.fail_with = TtsError("TTS_TIMEOUT", retryable=True)
    with pytest.raises(TtsError):
        await _handle(shop, event)
    tts.fail_with = TtsError("TTS_TEXT_REFUSED", retryable=False)
    await _handle(shop, event)  # logged, nothing made, not retried
    assert await _intros(shop) == []
    tts.fail_with = None
    await _handle(shop, event)
    (made,) = await _intros(shop)
    assert made.state == "READY" and made.previewed_at is None


async def test_without_storage_nothing_is_stored(
    client: AsyncClient, live_voice: MockVoiceTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intro, "audio_storage", lambda: None)
    shop = await _shop(client, "01700000014")
    response = await _upload(client, shop["tenant_id"])
    assert response.status_code == 503 and response.json()["code"] == "AUDIO_STORAGE_NOT_CONFIGURED"
    assert await _intros(shop) == []


# -------------------------------------------------------------- the calls ---


async def test_calls_play_the_intro_the_seller_turned_on(
    client: AsyncClient, live_voice: MockVoiceTransport, store: Store
) -> None:
    shop = await _shop(client, "01700000017")
    tenant = shop["tenant_id"]
    order = await _queued(client, shop, "01700000018")

    await jobs.dispatch_confirmation_calls()  # no intro yet: the seller's queue keeps it
    assert live_voice.calls == [] and live_voice.uploads == []
    assert (await _confirmation(shop, order)).state == "CALL_QUEUED"

    assert (await _upload(client, tenant)).status_code == 200
    assert (await _listen_and_turn_on(client, shop, 1)).status_code == 200

    live_voice.script_audio(VoiceAudioOutcome.FAILED)
    await jobs.dispatch_confirmation_calls()  # the provider refused the file this tick
    assert live_voice.calls == [] and (await _confirmation(shop, order)).state == "CALL_QUEUED"

    await jobs.dispatch_confirmation_calls()
    (uploaded,) = live_voice.uploads
    assert uploaded.key == f"voice/{tenant}/intro-v1.wav" and uploaded.content == _wav()
    (on,) = await _intros(shop)
    ref = on.provider_refs["mock"]
    (call,) = live_voice.calls
    # No amount prompts configured: the intro alone, by the provider's own id.
    assert call.audio_ref == ref and call.segments == ()
    assert (await _confirmation(shop, order)).state == "CALLING"
    status = await _status(client, shop)
    assert status["calls_on"] is True and status["calls_off_reason"] is None

    # A second order reuses the uploaded file.
    await _queued(client, shop, "01700000020")
    await jobs.dispatch_confirmation_calls()
    assert len(live_voice.uploads) == 1 and len(live_voice.calls) == 2

    # Turned off → calls stop; rows wait in the manual queue.
    await client.post("/v1/voice/intro/turn-off", headers=auth_header(shop))
    third = await _queued(client, shop, "01700000027")
    await jobs.dispatch_confirmation_calls()
    assert len(live_voice.calls) == 2 and (await _confirmation(shop, third)).state == "CALL_QUEUED"


async def test_the_amount_is_spoken_only_with_the_operator_prompts(
    client: AsyncClient,
    live_voice: MockVoiceTransport,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import reset_settings_cache

    monkeypatch.setenv("VOICE_AMOUNT_PREFIX_AUDIO_REF", "voice/platform/amount-pre.wav")
    monkeypatch.setenv("VOICE_AMOUNT_SUFFIX_AUDIO_REF", "voice/platform/amount-post.wav")
    reset_settings_cache()
    try:
        shop = await _shop(client, "01700000028")
        assert (await _upload(client, shop["tenant_id"])).status_code == 200
        assert (await _listen_and_turn_on(client, shop, 1)).status_code == 200
        order = await _queued(client, shop, "01700000029")
        await jobs.dispatch_confirmation_calls()
        (call,) = live_voice.calls
        cod = (await _confirmation(shop, order)).cod_paisa_snapshot
        (on,) = await _intros(shop)
        assert cod > 0 and cod % 100 == 0  # the default order: whole taka
        assert call.audio_ref is None
        assert [(s.kind, s.audio_ref, s.number) for s in call.segments] == [
            (SegmentKind.AUDIO, on.provider_refs["mock"], None),
            (SegmentKind.AUDIO, "voice/platform/amount-pre.wav", None),
            (SegmentKind.NUMBER, None, cod // 100),
            (SegmentKind.AUDIO, "voice/platform/amount-post.wav", None),
        ]
    finally:
        monkeypatch.delenv("VOICE_AMOUNT_PREFIX_AUDIO_REF")
        monkeypatch.delenv("VOICE_AMOUNT_SUFFIX_AUDIO_REF")
        reset_settings_cache()


async def test_one_recording_is_on_per_shop_in_the_database(
    client: AsyncClient, live_voice: MockVoiceTransport, store: Store
) -> None:
    from sqlalchemy.exc import IntegrityError

    shop = await _shop(client, "01700000030")
    await _upload(client, shop["tenant_id"])
    await _upload(client, shop["tenant_id"], content=_wav(0.6))
    await _listen_and_turn_on(client, shop, 2)
    with _Tenant(shop):
        async with session_scope() as db:
            first = await db.scalar(sa.select(VoiceIntroAudio).where(VoiceIntroAudio.version == 1))
            assert first is not None
            first.state = str(IntroState.ON)
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()
