"""Тесты energy-based VAD в Orchestrator._collect_utterance (Phase B).

Используем реальный Orchestrator с mock-провайдерами — _collect_utterance
трогает только self.settings и переданную AudioSession, остальные зависимости
ему не нужны, но конструктору Orchestrator они нужны как аргументы.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from phoneagent.config import get_settings
from phoneagent.connectors.mock import MockBookingConnector
from phoneagent.core.agent import MockLLMAgent
from phoneagent.core.audio_session import AudioSession
from phoneagent.core.orchestrator import Orchestrator
from phoneagent.core.state_store import InMemoryStateStore
from phoneagent.providers.stt.mock import MockSTTProvider
from phoneagent.providers.telephony.mock import MockTelephonyProvider
from phoneagent.providers.tts.mock import MockTTSProvider

# RMS порога 400 для этих тестов: "тихий" чанк — почти нули (RMS ~0..неск.
# единиц), "громкий" — амплитуда с запасом выше порога.
LOUD_CHUNK = (2000).to_bytes(2, "little", signed=True) * 160  # 20ms @ 8kHz
QUIET_CHUNK = (0).to_bytes(2, "little", signed=True) * 160


def _mock_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for key in ("TELEPHONY_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER", "BOOKING_CONNECTOR"):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("STATE_STORE", "memory")
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _build_orchestrator() -> Orchestrator:
    return Orchestrator(
        telephony=MockTelephonyProvider(),
        stt=MockSTTProvider(),
        tts=MockTTSProvider(),
        llm=MockLLMAgent(),
        booking=MockBookingConnector(),
        state_store=InMemoryStateStore(),
    )


@pytest.mark.asyncio
async def test_collect_utterance_gives_up_on_total_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch, VAD_INITIAL_SILENCE_SECONDS="0.1", VAD_SILENCE_SECONDS="1.0")
    orchestrator = _build_orchestrator()
    session = AudioSession("call-vad-1")

    async def feed() -> None:
        for _ in range(3):
            await asyncio.sleep(0.03)
            session.push_incoming(QUIET_CHUNK)

    task = asyncio.create_task(feed())
    result = await orchestrator._collect_utterance(session)
    await task

    assert result == b""


@pytest.mark.asyncio
async def test_collect_utterance_returns_buffer_after_speech_and_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_env(monkeypatch, VAD_INITIAL_SILENCE_SECONDS="2.0", VAD_SILENCE_SECONDS="0.1")
    orchestrator = _build_orchestrator()
    session = AudioSession("call-vad-2")

    async def feed() -> None:
        session.push_incoming(LOUD_CHUNK)
        await asyncio.sleep(0.02)
        session.push_incoming(LOUD_CHUNK)
        # дальше тишина — molчание длиннее VAD_SILENCE_SECONDS=0.1 должно
        # завершить сбор реплики без явного push'а QUIET_CHUNK.

    task = asyncio.create_task(feed())
    result = await orchestrator._collect_utterance(session)
    await task

    assert result == LOUD_CHUNK * 2


@pytest.mark.asyncio
async def test_collect_utterance_stops_on_session_close_mid_utterance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_env(monkeypatch, VAD_INITIAL_SILENCE_SECONDS="2.0", VAD_SILENCE_SECONDS="5.0")
    orchestrator = _build_orchestrator()
    session = AudioSession("call-vad-3")

    async def feed() -> None:
        session.push_incoming(LOUD_CHUNK)
        await asyncio.sleep(0.02)
        session.close()  # звонок оборвался посреди реплики

    task = asyncio.create_task(feed())
    result = await orchestrator._collect_utterance(session)
    await task

    assert result == LOUD_CHUNK


@pytest.mark.asyncio
async def test_collect_utterance_respects_max_utterance_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(
        monkeypatch,
        VAD_INITIAL_SILENCE_SECONDS="2.0",
        VAD_SILENCE_SECONDS="1.0",  # больше паузы между чанками — тишина не должна триггерить
        VAD_MAX_UTTERANCE_SECONDS="0.15",
    )
    orchestrator = _build_orchestrator()
    session = AudioSession("call-vad-4")
    stop = asyncio.Event()

    async def feed() -> None:
        while not stop.is_set():
            session.push_incoming(LOUD_CHUNK)
            await asyncio.sleep(0.02)

    task = asyncio.create_task(feed())
    start = time.monotonic()
    result = await orchestrator._collect_utterance(session)
    elapsed = time.monotonic() - start
    stop.set()
    await task

    assert result  # что-то собрать успело
    # Непрерывная громкая речь без тишины — должно оборваться по max_utterance
    # (~0.15s), а не висеть до силы вечности.
    assert elapsed < 1.0
