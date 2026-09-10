"""Аудио-мост Voxengine: Phase B — реальная передача через AudioSession.

Авторизация (WEBHOOK_SECRET) тестируется отдельно от передачи аудио — тот
транспортный слой Phase B не менял.
"""

from __future__ import annotations

import base64
import time

import pytest
from fastapi.testclient import TestClient

from phoneagent.config import get_settings
from phoneagent.core.audio_session import get_session, remove_session


def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("TELEPHONY_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER", "BOOKING_CONNECTOR"):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("STATE_STORE", "memory")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    get_settings.cache_clear()


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_incoming_media_reaches_audio_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    from phoneagent.main import create_app

    call_id = "test-incoming-1"
    payload = base64.b64encode(b"\x11\x22\x33\x44").decode()
    try:
        with TestClient(create_app()) as client, client.websocket_connect(f"/ws/voxengine/{call_id}") as ws:
            ws.send_json(
                {
                    "event": "start",
                    "sequenceNumber": 0,
                    "start": {"mediaFormat": {}, "customParameters": {}},
                }
            )
            ws.send_json(
                {"event": "media", "sequenceNumber": 1, "media": {"chunk": 1, "timestamp": 20, "payload": payload}}
            )

            session = get_session(call_id)
            assert session is not None
            assert _wait_until(lambda: not session.incoming.empty())
            assert session.incoming.get_nowait() == b"\x11\x22\x33\x44"

            ws.send_json({"event": "stop", "sequenceNumber": 2, "stop": {"mediaInfo": {}}})
    finally:
        remove_session(call_id)
        get_settings.cache_clear()


def test_outgoing_audio_session_reaches_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    from phoneagent.main import create_app

    call_id = "test-outgoing-1"
    try:
        with TestClient(create_app()) as client, client.websocket_connect(f"/ws/voxengine/{call_id}") as ws:
            session = get_session(call_id)
            assert session is not None
            # Кадр 320 байт (20ms @ 8kHz PCM16 mono, дефолтные настройки).
            session.push_outgoing(b"\x05" * 320)

            # Наша сторона отвечает своим "start" только в ответ на входящий
            # "start" (не сразу при коннекте — было гонкой, см. коммент в
            # media_ws.py), поэтому шлём его сами, как это делает VoxEngine.
            ws.send_json(
                {
                    "event": "start",
                    "sequenceNumber": 0,
                    "start": {"mediaFormat": {"encoding": "PCM16", "sampleRate": 8000}, "customParameters": {}},
                }
            )

            start_msg = ws.receive_json()
            assert start_msg["event"] == "start"
            assert start_msg["start"]["mediaFormat"]["sampleRate"] == 8000

            received = ws.receive_json()
            assert received["event"] == "media"
            assert base64.b64decode(received["media"]["payload"]) == b"\x05" * 320

            ws.send_json({"event": "stop", "sequenceNumber": 0, "stop": {"mediaInfo": {}}})
    finally:
        remove_session(call_id)
        get_settings.cache_clear()


def test_bridge_rejects_wrong_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_bridge_accepts_correct_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    monkeypatch.setenv("WEBHOOK_SECRET", "correct-secret")
    get_settings.cache_clear()
    from phoneagent.main import create_app

    app = create_app()
    call_id = "test-call-3"
    try:
        with TestClient(app) as client, client.websocket_connect(
            f"/ws/voxengine/{call_id}", headers={"X-Webhook-Secret": "correct-secret"}
        ) as ws:
            ws.send_json({"event": "stop", "sequenceNumber": 0, "stop": {"mediaInfo": {}}})
    finally:
        remove_session(call_id)
        get_settings.cache_clear()


def test_bridge_ignores_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    get_settings.cache_clear()
    from phoneagent.main import create_app

    call_id = "test-call-4"
    try:
        with TestClient(create_app()) as client, client.websocket_connect(f"/ws/voxengine/{call_id}") as ws:
            ws.send_text("not json{{{")
            ws.send_json({"event": "stop", "sequenceNumber": 0, "stop": {"mediaInfo": {}}})
    finally:
        remove_session(call_id)
        get_settings.cache_clear()


def test_bridge_removes_session_on_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch)
    get_settings.cache_clear()
    from phoneagent.main import create_app

    call_id = "test-call-5"
    with TestClient(create_app()) as client, client.websocket_connect(f"/ws/voxengine/{call_id}") as ws:
        assert get_session(call_id) is not None
        ws.send_json({"event": "stop", "sequenceNumber": 0, "stop": {"mediaInfo": {}}})

    assert _wait_until(lambda: get_session(call_id) is None)
    get_settings.cache_clear()
