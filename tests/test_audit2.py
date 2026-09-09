"""Регрессионные тесты второго прохода аудита: промпт, эскалация, таймауты,
дедуп, fail-closed, PII, аудио-контракт, state store."""

from __future__ import annotations

import asyncio
from datetime import datetime

import fakeredis.aioredis
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from phoneagent.api.security import secret_matches
from phoneagent.config import get_settings
from phoneagent.connectors.mock import MockBookingConnector
from phoneagent.core import state_store as state_store_module
from phoneagent.core.agent import TOOL_DESCRIPTIONS, MockLLMAgent
from phoneagent.core.orchestrator import DuplicateCallbackError, Orchestrator
from phoneagent.core.prompts import get_system_prompt
from phoneagent.core.state_store import InMemoryStateStore, RedisStateStore
from phoneagent.models.conversation import ConversationState, ConversationStep
from phoneagent.providers.stt.mock import MockSTTProvider
from phoneagent.providers.telephony.mock import MockTelephonyProvider
from phoneagent.providers.tts.elevenlabs import ElevenLabsTTSProvider
from phoneagent.providers.tts.mock import MockTTSProvider
from phoneagent.utils import mask_phone, redact_url

PHONE = "+79991234567"


# ── Промпт ──────────────────────────────────────────────


def test_prompt_injects_date_salon_and_phone_rule() -> None:
    prompt = get_system_prompt(
        "ru", salon_name="Тестовый салон", working_hours="09:00-21:00",
        now=datetime(2026, 9, 10, 12, 30),
    )
    assert "2026-09-10" in prompt
    assert "четверг" in prompt
    assert "12:30" in prompt
    assert "Тестовый салон" in prompt
    assert "09:00-21:00" in prompt
    assert "НЕ спрашивай" in prompt
    assert "escalate_to_human" in prompt
    assert "Жарвис" not in prompt


def test_prompt_en_weekday() -> None:
    prompt = get_system_prompt("en", now=datetime(2026, 9, 12, 9, 0))
    assert "Saturday" in prompt and "2026-09-12" in prompt


def test_tool_schema_has_no_client_phone_and_has_escalation() -> None:
    by_name = {t["name"]: t for t in TOOL_DESCRIPTIONS}
    booking = by_name["create_booking"]["input_schema"]
    assert "client_phone" not in booking["properties"]
    assert "client_phone" not in booking["required"]
    assert "escalate_to_human" in by_name


# ── Голосовой цикл Orchestrator ─────────────────────────


class ScriptedOrchestrator(Orchestrator):
    """_listen отдаёт заранее заданные реплики — единственный способ прогнать
    _process_call без реальной телефонии."""

    def __init__(self, replies: list[str], listen_delay: float = 0.0, **kw):  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self._replies = list(replies)
        self._listen_delay = listen_delay

    async def _listen(self, state: ConversationState) -> str:
        await asyncio.sleep(self._listen_delay)
        return self._replies.pop(0) if self._replies else ""


async def _make_orchestrator(replies: list[str], **kw) -> ScriptedOrchestrator:  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    telephony = MockTelephonyProvider()
    await telephony.connect()
    booking = MockBookingConnector()
    await booking.connect()
    return ScriptedOrchestrator(
        replies,
        telephony=telephony,
        stt=MockSTTProvider(),
        tts=MockTTSProvider(),
        llm=MockLLMAgent(),
        booking=booking,
        state_store=InMemoryStateStore(),
        **kw,
    )


async def _run_call(orch: Orchestrator, phone: str = PHONE) -> ConversationState:
    call_id = await orch.handle_callback(phone, "demo")
    await asyncio.gather(*orch._background_tasks)
    state = await orch.state_store.get(call_id)
    assert state is not None
    return state


@pytest.mark.asyncio
async def test_voice_loop_escalates_and_hangs_up() -> None:
    orch = await _make_orchestrator(["Здравствуйте", "Позовите администратора"])
    state = await _run_call(orch)
    assert state.step == ConversationStep.ESCALATE
    assert state.escalation_reason == "client asked"
    status = await orch.telephony.get_status(state.call_id)
    assert status.status.value == "completed"


@pytest.mark.asyncio
async def test_voice_loop_gives_up_after_max_retries_of_silence() -> None:
    orch = await _make_orchestrator([])
    state = await _run_call(orch)
    assert state.step == ConversationStep.END
    assert state.retry_count == orch.settings.max_retries


@pytest.mark.asyncio
async def test_voice_loop_enforces_call_timeout() -> None:
    orch = await _make_orchestrator(["Здравствуйте"] * 50, listen_delay=0.3)
    orch.settings = orch.settings.model_copy(update={"call_timeout_seconds": 1})
    state = await _run_call(orch)
    assert state.step == ConversationStep.END
    status = await orch.telephony.get_status(state.call_id)
    assert status.status.value == "completed"


@pytest.mark.asyncio
async def test_callback_deduplicated_within_window() -> None:
    orch = await _make_orchestrator([])
    await orch.handle_callback(PHONE, "demo")
    with pytest.raises(DuplicateCallbackError):
        await orch.handle_callback(PHONE, "demo")
    await asyncio.gather(*orch._background_tasks)


@pytest.mark.asyncio
async def test_text_mode_escalation_ends_dialog() -> None:
    orch = await _make_orchestrator([])
    first = await orch.handle_text_message(
        call_id=None, salon_id="demo", client_phone=PHONE, language="ru", text="Здравствуйте"
    )
    second = await orch.handle_text_message(
        call_id=first["call_id"], salon_id="demo", client_phone=PHONE, language="ru",
        text="хочу человека",
    )
    assert second["step"] == "escalate"
    third = await orch.handle_text_message(
        call_id=first["call_id"], salon_id="demo", client_phone=PHONE, language="ru", text="алло",
    )
    assert third["reply"] == "" and third["step"] == "escalate"


# ── State store ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_store_ttl_and_lock() -> None:
    store = InMemoryStateStore()
    state = ConversationState(call_id="c", salon_id="demo", client_phone=PHONE)
    await store.set(state, ttl_seconds=0)
    assert await store.get("c") is None
    await store.set(state, ttl_seconds=60)
    assert await store.get("c") is not None
    assert await store.acquire_lock("k", 60) is True
    assert await store.acquire_lock("k", 60) is False


@pytest.mark.asyncio
async def test_redis_store_roundtrip_and_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(state_store_module.aioredis, "from_url", lambda *a, **k: fake)
    store = RedisStateStore("redis://:secret@localhost:6379/0")
    await store.connect()
    state = ConversationState(call_id="r1", salon_id="demo", client_phone=PHONE, step=ConversationStep.ASK_DATE)
    await store.set(state, ttl_seconds=60)
    loaded = await store.get("r1")
    assert loaded is not None and loaded.step == ConversationStep.ASK_DATE
    assert loaded.created_at == state.created_at
    assert await store.acquire_lock("x", 60) is True
    assert await store.acquire_lock("x", 60) is False
    await store.delete("r1")
    assert await store.get("r1") is None


# ── PII / security helpers ──────────────────────────────


def test_mask_phone_and_redact_url() -> None:
    assert mask_phone(PHONE) == "+7999***4567"
    assert mask_phone("123") == "***"
    assert redact_url("redis://:s3cret@host:6379/0") == "redis://:***@host:6379/0"
    assert redact_url("redis://host:6379/0") == "redis://host:6379/0"


def test_secret_matches_non_ascii_does_not_raise() -> None:
    assert secret_matches("сёкрет", "secret") is False
    assert secret_matches("secret", "secret") is True
    assert secret_matches(None, "secret") is False


# ── FastAPI app ─────────────────────────────────────────


def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("TELEPHONY_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER", "BOOKING_CONNECTOR"):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("STATE_STORE", "memory")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    get_settings.cache_clear()


def test_production_refuses_to_start_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    from phoneagent.main import create_app

    with pytest.raises(RuntimeError, match="API_AUTH_TOKEN, WEBHOOK_SECRET"), TestClient(create_app()):
        pass
    get_settings.cache_clear()


def test_phone_validation_and_dedupe_via_api(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    from phoneagent.main import create_app

    with TestClient(create_app()) as client:
        assert client.post("/call/request-callback", json={"phone": "12345"}).status_code == 422
        assert client.post("/call/text", json={"text": "hi"}).status_code == 422
        assert client.post("/call/request-callback", json={"phone": PHONE}).status_code == 202
        assert client.post("/call/request-callback", json={"phone": PHONE}).status_code == 409
    get_settings.cache_clear()


def test_admin_config_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_TOKEN", "t")
    get_settings.cache_clear()
    from phoneagent.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/admin/health").status_code == 200
        assert client.get("/admin/config").status_code == 401
        assert client.get("/admin/config", headers={"Authorization": "Bearer t"}).status_code == 200
    get_settings.cache_clear()


# ── Аудио-контракт TTS ──────────────────────────────────


@pytest.mark.asyncio
async def test_elevenlabs_requests_pcm_at_target_rate() -> None:
    provider = ElevenLabsTTSProvider()
    provider.settings = provider.settings.model_copy(
        update={"elevenlabs_api_key": "k", "elevenlabs_voice_id": "v"}
    )
    pcm = b"\x00\x01" * 800
    async with respx.mock(base_url="https://api.elevenlabs.io/v1") as mock:
        route = mock.post("/text-to-speech/v").mock(return_value=httpx.Response(200, content=pcm))
        await provider.connect()
        try:
            out = await provider.synthesize("Привет", sample_rate=8000)
        finally:
            await provider.disconnect()
    assert route.calls[0].request.url.params["output_format"] == "pcm_8000"
    assert out == pcm
