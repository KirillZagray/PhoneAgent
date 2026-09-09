"""Регрессионные тесты на баги, найденные и исправленные в тех-аудите."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from phoneagent.config import get_settings
from phoneagent.connectors.generic_api import GenericAPIBookingConnector
from phoneagent.core.agent import AnthropicLLMAgent
from phoneagent.core.orchestrator import Orchestrator
from phoneagent.models.booking import BookingRequest
from phoneagent.models.conversation import ConversationState
from phoneagent.providers.stt.voicestudio import VoiceStudioSTTProvider
from phoneagent.providers.telephony.mock import MockTelephonyProvider
from phoneagent.providers.telephony.twilio import TwilioTelephonyProvider
from phoneagent.providers.telephony.voximplant import VoximplantTelephonyProvider
from phoneagent.providers.tts.voicestudio import VoiceStudioTTSProvider
from phoneagent.utils.audio import pcm_to_wav

# ── generic_api.py: create_booking больше не шлёт пустое тело ─────────────


@pytest.mark.asyncio
async def test_generic_api_create_booking_sends_real_body():
    connector = GenericAPIBookingConnector()
    connector.settings.booking_api_url = "http://salon.example"
    sent_body: dict = {}

    async with respx.mock(base_url="http://salon.example") as mock:
        def capture(request: httpx.Request) -> httpx.Response:
            import json as _json

            sent_body.update(_json.loads(request.content))
            return httpx.Response(200, json={"data": {"id": "b1"}})

        mock.post("/bookings").mock(side_effect=capture)

        await connector.connect()
        try:
            req = BookingRequest(
                service_id="haircut",
                master_id="m1",
                date=date(2026, 3, 15),
                time="14:00",
                client_phone="+79991234567",
                client_name="Аня",
            )
            booking = await connector.create_booking(req)
            assert booking.id == "b1"
        finally:
            await connector.disconnect()

    assert sent_body["master_id"] == "m1"
    assert sent_body["service_id"] == "haircut"
    assert sent_body["client_phone"] == "+79991234567"
    assert sent_body["time"] == "14:00"


@pytest.mark.asyncio
async def test_generic_api_body_template_substitution():
    """Явный шаблон body из конфига (см. докстринг класса) подставляется корректно."""
    config = {
        "create_booking": {
            "method": "POST",
            "path": "/bookings",
            "body": {"master": "{master_id}", "note": "booked via {service_id}"},
        }
    }
    connector = GenericAPIBookingConnector(config=config)
    connector.settings.booking_api_url = "http://salon.example"
    sent_body: dict = {}

    async with respx.mock(base_url="http://salon.example") as mock:
        def capture(request: httpx.Request) -> httpx.Response:
            import json as _json

            sent_body.update(_json.loads(request.content))
            return httpx.Response(200, json={"id": "b1"})

        mock.post("/bookings").mock(side_effect=capture)

        await connector.connect()
        try:
            req = BookingRequest(
                service_id="haircut",
                master_id="m1",
                date=date(2026, 3, 15),
                time="14:00",
                client_phone="+79991234567",
            )
            await connector.create_booking(req)
        finally:
            await connector.disconnect()

    assert sent_body == {"master": "m1", "note": "booked via haircut"}


# ── VoiceStudio TTS: правильный OpenAI-совместимый эндпоинт ───────────────


@pytest.mark.asyncio
async def test_voicestudio_tts_hits_correct_endpoint():
    provider = VoiceStudioTTSProvider()
    provider.settings.voicestudio_url = "http://localhost:3900"  # type: ignore[assignment]

    async with respx.mock(base_url="http://localhost:3900") as mock:
        mock.get("/v1/audio/voices").mock(return_value=httpx.Response(200, json=[]))
        # Реальный WAV 24 kHz (как отдают движки VoiceStudio) — провайдер обязан
        # привести его к PCM 8 kHz по контракту пайплайна.
        wav_24k = pcm_to_wav(b"\x00\x01" * 24000, sample_rate=24000)
        speech_route = mock.post("/v1/audio/speech").mock(
            return_value=httpx.Response(200, content=wav_24k)
        )

        await provider.connect()
        try:
            audio = await provider.synthesize("Привет", language="ru")
        finally:
            await provider.disconnect()

    assert speech_route.called
    request = speech_route.calls[0].request
    import json as _json

    payload = _json.loads(request.content)
    assert payload["model"] == "tts-1"
    assert payload["input"] == "Привет"
    assert not audio.startswith(b"RIFF")  # сырой PCM, без заголовка
    assert abs(len(audio) - 8000 * 2) <= 4  # 1 секунда PCM s16 mono @ 8 kHz


# ── VoiceStudio STT: та же связка Whisper/VoiceStudio, что просил юзер ─────


@pytest.mark.asyncio
async def test_voicestudio_stt_hits_transcriptions_endpoint():
    provider = VoiceStudioSTTProvider()
    provider.settings.voicestudio_url = "http://localhost:3900"  # type: ignore[assignment]

    async with respx.mock(base_url="http://localhost:3900") as mock:
        mock.get("/v1/audio/voices").mock(return_value=httpx.Response(200, json=[]))
        transcribe_route = mock.post("/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "хочу записаться на стрижку"})
        )

        await provider.connect()
        try:
            # 1 секунда тишины 8kHz mono 16-bit PCM — этого достаточно, чтобы
            # проверить, что запрос реально уходит на правильный эндпоинт.
            text = await provider.transcribe(b"\x00\x00" * 8000, sample_rate=8000)
        finally:
            await provider.disconnect()

    assert transcribe_route.called
    assert text == "хочу записаться на стрижку"


# ── Orchestrator: реальный звонок не размещается, если провайдер это не умеет ──


@pytest.mark.asyncio
async def test_handle_callback_refuses_provider_without_realtime_audio():
    twilio = TwilioTelephonyProvider()
    orchestrator = Orchestrator(
        telephony=twilio,
        stt=AsyncMock(),
        tts=AsyncMock(),
        llm=AsyncMock(),
        booking=AsyncMock(),
        state_store=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="does not implement real-time audio"):
        await orchestrator.handle_callback("+79991234567", "demo")


@pytest.mark.asyncio
async def test_handle_callback_allows_mock_provider():
    mock_telephony = MockTelephonyProvider()
    await mock_telephony.connect()
    state_store = AsyncMock()

    orchestrator = Orchestrator(
        telephony=mock_telephony,
        stt=AsyncMock(),
        tts=AsyncMock(),
        llm=AsyncMock(),
        booking=AsyncMock(),
        state_store=state_store,
    )
    call_id = await orchestrator.handle_callback("+79991234567", "demo")
    assert call_id.startswith("mock-")
    state_store.set.assert_awaited()


# ── Anthropic-агент: tool_result уходит правильным content-блоком ─────────


@pytest.mark.asyncio
async def test_anthropic_agent_sends_proper_tool_result_blocks():
    """Раньше результат tool call превращался в обычный текст без tool_use_id —
    Anthropic API отклонил бы такую историю при нескольких tool calls за turn.
    """

    class FakeBlock:
        def __init__(self, type_: str, **kw):
            self.type = type_
            for k, v in kw.items():
                setattr(self, k, v)

    class FakeResponse:
        def __init__(self, content, stop_reason):
            self.content = content
            self.stop_reason = stop_reason

    agent = AnthropicLLMAgent()
    agent.settings.anthropic_api_key = "sk-ant-test"
    agent._client = AsyncMock()

    tool_use_block = FakeBlock("tool_use", id="toolu_1", name="list_services", input={})
    first_response = FakeResponse([tool_use_block], stop_reason="tool_use")
    final_response = FakeResponse(
        [FakeBlock("text", text="Вот доступные услуги.")], stop_reason="end_turn"
    )
    agent._client.messages.create = AsyncMock(side_effect=[first_response, final_response])

    async def execute_tool(name: str, args: dict) -> dict:
        return {"services": ["haircut"]}

    state = ConversationState(call_id="c1", salon_id="demo", client_phone="+7")
    response = await agent.run_turn(state, "Какие у вас услуги?", [], execute_tool=execute_tool)

    assert response.text == "Вот доступные услуги."
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "list_services"

    # Второй вызов create() должен получить историю с корректным tool_result блоком
    second_call_messages = agent._client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result_message = second_call_messages[-1]
    assert tool_result_message["role"] == "user"
    assert tool_result_message["content"][0]["type"] == "tool_result"
    assert tool_result_message["content"][0]["tool_use_id"] == "toolu_1"


# ── FastAPI app: /call/text работает end-to-end без Redis/реальных провайдеров ──


def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "TELEPHONY_PROVIDER",
        "STT_PROVIDER",
        "TTS_PROVIDER",
        "LLM_PROVIDER",
        "BOOKING_CONNECTOR",
    ):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("STATE_STORE", "memory")
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    get_settings.cache_clear()


def test_text_endpoint_full_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    from phoneagent.main import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/call/text", json={"text": "Здравствуйте", "phone": "+79991234567"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["call_id"]
        assert data["step"] == "ask_service"

        resp2 = client.post(
            "/call/text", json={"text": "Хочу стрижку", "call_id": data["call_id"]}
        )
        assert resp2.status_code == 200
        assert resp2.json()["call_id"] == data["call_id"]

        resp3 = client.post("/call/text", json={"text": "привет", "call_id": "nope"})
        assert resp3.status_code == 404
    get_settings.cache_clear()


def test_request_callback_requires_auth_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_TOKEN", "secret123")
    get_settings.cache_clear()
    from phoneagent.main import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/call/request-callback", json={"phone": "+79991234567"})
        assert resp.status_code == 401

        resp_ok = client.post(
            "/call/request-callback",
            json={"phone": "+79991234567"},
            headers={"Authorization": "Bearer secret123"},
        )
        assert resp_ok.status_code == 202
    get_settings.cache_clear()


def test_webhook_requires_secret_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    monkeypatch.setenv("WEBHOOK_SECRET", "whsec")
    get_settings.cache_clear()
    from phoneagent.main import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/webhooks/twilio", json={})
        assert resp.status_code == 401

        resp_ok = client.post(
            "/webhooks/twilio", json={}, headers={"X-Webhook-Secret": "whsec"}
        )
        assert resp_ok.status_code == 200
    get_settings.cache_clear()


# ── Voximplant: реальный Management API (StartScenarios, не выдуманный StartCall) ──


@pytest.mark.asyncio
async def test_voximplant_make_call_uses_start_scenarios_with_pipe_custom_data():
    provider = VoximplantTelephonyProvider()
    provider.settings = provider.settings.model_copy(
        update={"voximplant_account_id": "1", "voximplant_api_key": "k", "voximplant_rule_id": "5"}
    )
    sent_data: dict = {}

    async with respx.mock(base_url="https://api.voximplant.com/platform_api") as mock:
        def capture(request: httpx.Request) -> httpx.Response:
            from urllib.parse import parse_qsl

            sent_data.update(dict(parse_qsl(request.content.decode())))
            return httpx.Response(
                200,
                json={
                    "result": 1,
                    "call_session_history_id": 12345,
                    "media_session_access_secure_url": "https://example.com/manage/abc",
                },
            )

        mock.post("/StartScenarios").mock(side_effect=capture)

        await provider.connect()
        try:
            ref = await provider.make_call("+79991234567")
        finally:
            await provider.disconnect()

    assert sent_data["rule_id"] == "5"
    # customData — "call_id|phone", НЕ json/base64 (лимит 200 байт у VoxEngine.customData()).
    call_id, phone = sent_data["script_custom_data"].split("|")
    assert phone == "+79991234567"
    assert ref.call_id == call_id
    assert ref.metadata["voximplant_session_id"] == "12345"


@pytest.mark.asyncio
async def test_voximplant_hangup_calls_media_session_access_url():
    provider = VoximplantTelephonyProvider()
    provider.settings = provider.settings.model_copy(
        update={"voximplant_account_id": "1", "voximplant_api_key": "k", "voximplant_rule_id": "5"}
    )

    async with respx.mock() as mock:
        mock.post("https://api.voximplant.com/platform_api/StartScenarios").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": 1,
                    "call_session_history_id": 1,
                    "media_session_access_secure_url": "https://vx.example/manage/xyz",
                },
            )
        )
        manage_route = mock.get("https://vx.example/manage/xyz").mock(return_value=httpx.Response(200))

        await provider.connect()
        try:
            ref = await provider.make_call("+79991234567")
            await provider.hangup(ref.call_id)
        finally:
            await provider.disconnect()

    assert manage_route.called
