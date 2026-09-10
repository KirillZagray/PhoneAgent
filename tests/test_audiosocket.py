"""Тесты AudioSocket-моста (Phase B — реальная передача через AudioSession)."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from phoneagent.api.audiosocket_server import (
    KIND_AUDIO,
    KIND_HANGUP,
    KIND_UUID,
    _read_frame,
    _write_frame,
    start_audiosocket_server,
)
from phoneagent.config import get_settings
from phoneagent.core.audio_session import get_session, remove_session


def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("TELEPHONY_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER", "BOOKING_CONNECTOR"):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("STATE_STORE", "memory")
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    get_settings.cache_clear()


def test_write_frame_format() -> None:
    class FakeWriter:
        def __init__(self) -> None:
            self.written = b""

        def write(self, data: bytes) -> None:
            self.written += data

    writer = FakeWriter()
    _write_frame(writer, KIND_AUDIO, b"\x01\x02\x03")  # type: ignore[arg-type]
    assert writer.written == bytes([KIND_AUDIO]) + b"\x00\x03" + b"\x01\x02\x03"


@pytest.mark.asyncio
async def test_read_frame_roundtrip() -> None:
    reader = asyncio.StreamReader()
    payload = b"hello-pcm"
    reader.feed_data(bytes([KIND_AUDIO]) + len(payload).to_bytes(2, "big") + payload)
    reader.feed_eof()

    kind, data = await _read_frame(reader)  # type: ignore[misc]
    assert kind == KIND_AUDIO
    assert data == payload


@pytest.mark.asyncio
async def test_read_frame_empty_payload() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(bytes([KIND_HANGUP]) + b"\x00\x00")
    reader.feed_eof()

    kind, data = await _read_frame(reader)  # type: ignore[misc]
    assert kind == KIND_HANGUP
    assert data == b""


@pytest.mark.asyncio
async def test_audiosocket_incoming_audio_reaches_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """UUID-хендшейк создаёт AudioSession; входящий AUDIO-фрейм попадает в neё."""
    _mock_env(monkeypatch)
    server = await start_audiosocket_server("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr,index]
    call_id = uuid.uuid4()
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(bytes([KIND_UUID]) + (16).to_bytes(2, "big") + call_id.bytes)
            payload = b"\x00\x01" * 10
            writer.write(bytes([KIND_AUDIO]) + len(payload).to_bytes(2, "big") + payload)
            await writer.drain()

            session = None
            for _ in range(50):
                session = get_session(str(call_id))
                if session is not None and not session.incoming.empty():
                    break
                await asyncio.sleep(0.02)

            assert session is not None
            assert session.incoming.get_nowait() == payload
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        remove_session(str(call_id))
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_audiosocket_outgoing_session_audio_reaches_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Аудио, положенное в session.outgoing, доходит до клиента AudioSocket-фреймом."""
    _mock_env(monkeypatch)
    server = await start_audiosocket_server("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr,index]
    call_id = uuid.uuid4()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(bytes([KIND_UUID]) + (16).to_bytes(2, "big") + call_id.bytes)
            await writer.drain()

            session = None
            for _ in range(50):
                session = get_session(str(call_id))
                if session is not None:
                    break
                await asyncio.sleep(0.02)
            assert session is not None

            frame = b"\x07" * 320  # 20ms @ 8kHz PCM16 mono — дефолтный размер кадра
            session.push_outgoing(frame)

            kind, received = await asyncio.wait_for(_read_frame(reader), timeout=2.0)  # type: ignore[misc]
            assert kind == KIND_AUDIO
            assert received == frame
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        remove_session(str(call_id))
        server.close()
        await server.wait_closed()
