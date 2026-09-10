"""Тесты барж-ина (клиент перебивает ещё говорящего агента) — Orchestrator._wait_for_barge_in
и AudioSession.clear_outgoing. Разбор реальной записи звонка 2026-09-10 показал, что без
этого агент буквально договаривал свою фразу поверх клиента — см. комментарии в _say().
"""

from __future__ import annotations

import struct

import pytest

from phoneagent.config import get_settings
from phoneagent.connectors.mock import MockBookingConnector
from phoneagent.core.agent import MockLLMAgent
from phoneagent.core.audio_session import create_session, remove_session
from phoneagent.core.orchestrator import Orchestrator
from phoneagent.core.state_store import InMemoryStateStore
from phoneagent.providers.stt.mock import MockSTTProvider
from phoneagent.providers.telephony.mock import MockTelephonyProvider
from phoneagent.providers.tts.mock import MockTTSProvider

LOUD = struct.pack("<h", 3000) * 80  # rms ~3000, выше дефолтного порога 400
QUIET = b"\x00\x00" * 80  # rms 0


def _mock_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for key in ("TELEPHONY_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "BOOKING_CONNECTOR", "LLM_PROVIDER"):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("STATE_STORE", "memory")
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
async def test_wait_for_barge_in_confirms_after_sustained_loud_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_env(monkeypatch, BARGE_IN_GRACE_SECONDS="0", BARGE_IN_CONFIRM_CHUNKS="3")
    orchestrator = _build_orchestrator()
    session = create_session("call-barge-1")
    try:
        for chunk in (LOUD, LOUD, LOUD, QUIET):
            session.push_incoming(chunk)

        collected: list[bytes] = []
        barged_in = await orchestrator._wait_for_barge_in(session, collected)

        assert barged_in is True
        # Возвращается сразу после 3-го подтверждающего чанка — тишину после не читает.
        assert collected == [LOUD, LOUD, LOUD]
    finally:
        remove_session("call-barge-1")


@pytest.mark.asyncio
async def test_wait_for_barge_in_ignores_single_blip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Один громкий чанк (щелчок/эхо-всплеск) не должен обрывать агента —
    нужно устойчивое несколько подряд, иначе барж-ин будет ложно
    срабатывать на любой шум на линии без echo cancellation."""
    _mock_env(monkeypatch, BARGE_IN_GRACE_SECONDS="0", BARGE_IN_CONFIRM_CHUNKS="3")
    orchestrator = _build_orchestrator()
    session = create_session("call-barge-2")
    try:
        for chunk in (LOUD, QUIET, LOUD, None):  # None закрывает сессию, чтобы не висеть
            session.push_incoming(chunk) if chunk is not None else session.close()

        collected: list[bytes] = []
        barged_in = await orchestrator._wait_for_barge_in(session, collected)

        assert barged_in is False
        assert collected == [LOUD, QUIET, LOUD]
    finally:
        remove_session("call-barge-2")


@pytest.mark.asyncio
async def test_wait_for_barge_in_respects_grace_period(monkeypatch: pytest.MonkeyPatch) -> None:
    """Громкие чанки в течение grace-периода (хвост собственного эха агента
    сразу после начала фразы) не считаются барж-ином."""
    _mock_env(monkeypatch, BARGE_IN_GRACE_SECONDS="1000", BARGE_IN_CONFIRM_CHUNKS="2")
    orchestrator = _build_orchestrator()
    session = create_session("call-barge-3")
    try:
        for chunk in (LOUD, LOUD, LOUD):
            session.push_incoming(chunk)
        session.close()

        collected: list[bytes] = []
        barged_in = await orchestrator._wait_for_barge_in(session, collected)

        assert barged_in is False  # всё попало под гигантский grace-период
        assert collected == [LOUD, LOUD, LOUD]
    finally:
        remove_session("call-barge-3")


def test_clear_outgoing_drops_pending_chunks_and_marks_drained() -> None:
    session = create_session("call-barge-clear")
    try:
        session.push_outgoing(b"\x01")
        session.push_outgoing(b"\x02")
        assert not session._drained.is_set()

        session.clear_outgoing()

        assert session.outgoing.empty()
        assert session._pending_out == 0
        assert session._drained.is_set()
    finally:
        remove_session("call-barge-clear")
