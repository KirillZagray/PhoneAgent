"""Phase A аудио-моста: чистое эхо по /ws/voxengine/{call_id}.

См. docs/superpowers/specs/2026-09-09-voximplant-audio-bridge-design.md —
эта проверка не требует ни Voximplant, ни STT/TTS: только сам WS-транспорт
и формат сообщений (JSON start/media/stop).
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from phoneagent.config import get_settings


def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("TELEPHONY_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER", "BOOKING_CONNECTOR"):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("STATE_STORE", "memory")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    get_settings.cache_clear()


def test_echo_bridge_round_trips_media_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    from phoneagent.main import create_app

    payload = base64.b64encode(b"\x00\x01\x02\x03").decode()
    with TestClient(create_app()) as client, client.websocket_connect("/ws/voxengine/test-call-1") as ws:
        ws.send_json(
            {
                "event": "start",
                "sequenceNumber": 0,
                "start": {
                    "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                    "customParameters": {"call_id": "test-call-1"},
                },
            }
        )
        ws.send_json({"event": "media", "sequenceNumber": 1, "media": {"chunk": 1, "timestamp": 20, "payload": payload}})
        echoed = ws.receive_json()
        assert echoed["event"] == "media"
        assert echoed["media"]["payload"] == payload
        assert echoed["media"]["chunk"] == 1

        ws.send_json({"event": "stop", "sequenceNumber": 2, "stop": {"mediaInfo": {"bytesSent": 4, "duration": 1}}})
        # Роут должен закрыть соединение сам после stop.
        with pytest.raises(Exception):  # noqa: B017 — WebSocketDisconnect либо любой close-эквивалент клиента
            ws.receive_json()
    get_settings.cache_clear()


def test_echo_bridge_rejects_wrong_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    monkeypatch.setenv("WEBHOOK_SECRET", "correct-secret")
    get_settings.cache_clear()
    from phoneagent.main import create_app

    app = create_app()
    with (
        TestClient(app) as client,
        pytest.raises(Exception),  # noqa: B017 — rejected before accept()
        client.websocket_connect("/ws/voxengine/test-call-2", headers={"X-Webhook-Secret": "wrong"}),
    ):
        pass
    get_settings.cache_clear()


def test_echo_bridge_accepts_correct_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    monkeypatch.setenv("WEBHOOK_SECRET", "correct-secret")
    get_settings.cache_clear()
    from phoneagent.main import create_app

    app = create_app()
    with TestClient(app) as client, client.websocket_connect(
        "/ws/voxengine/test-call-3", headers={"X-Webhook-Secret": "correct-secret"}
    ) as ws:
        ws.send_json({"event": "stop", "sequenceNumber": 0, "stop": {"mediaInfo": {}}})
    get_settings.cache_clear()


def test_echo_bridge_ignores_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    get_settings.cache_clear()
    from phoneagent.main import create_app

    with TestClient(create_app()) as client, client.websocket_connect("/ws/voxengine/test-call-4") as ws:
        ws.send_text("not json{{{")
        ws.send_json({"event": "stop", "sequenceNumber": 0, "stop": {"mediaInfo": {}}})
    get_settings.cache_clear()
