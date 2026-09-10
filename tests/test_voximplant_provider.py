"""Тесты VoximplantTelephonyProvider — Phase B send_audio (остальное покрыто
интеграционно через ручной прогон Phase A/B на реальном аккаунте, см. README).
"""

from __future__ import annotations

import asyncio

import pytest

from phoneagent.core.audio_session import create_session, remove_session
from phoneagent.providers.telephony.voximplant import VoximplantTelephonyProvider


@pytest.mark.asyncio
async def test_hangup_inbound_call_requests_hangup_via_session() -> None:
    """Входящий звонок: нет _Session (StartScenarios не вызывался), поэтому
    hangup() должен упасть на AudioSession.request_hangup(), а не бросить
    исключение из-за отсутствующего media_session_access_secure_url.
    """
    provider = VoximplantTelephonyProvider()
    session = create_session("vox-inbound-1")
    try:
        await provider.hangup("vox-inbound-1")
        assert session.hangup_requested.is_set()
    finally:
        remove_session("vox-inbound-1")


@pytest.mark.asyncio
async def test_hangup_unknown_call_is_noop() -> None:
    provider = VoximplantTelephonyProvider()
    await provider.hangup("never-existed-anywhere")  # не должно бросить


def test_supports_realtime_audio_is_true() -> None:
    assert VoximplantTelephonyProvider.supports_realtime_audio is True


@pytest.mark.asyncio
async def test_send_audio_pushes_chunks_into_audio_session() -> None:
    provider = VoximplantTelephonyProvider()
    session = create_session("vox-call-1")
    try:

        async def _chunks():
            yield b"\xaa\xbb"
            yield b"\xcc\xdd"

        # send_audio() не возвращается, пока мост не заберёт чанки из
        # очереди (wait_drained) — гоним "мост" параллельно, иначе висит.
        drained: list[bytes] = []

        async def _drain_two() -> None:
            for _ in range(2):
                chunk = await session.outgoing.get()
                session.mark_outgoing_taken()
                drained.append(chunk)

        drain_task = asyncio.create_task(_drain_two())
        await asyncio.wait_for(provider.send_audio("vox-call-1", _chunks()), timeout=2.0)
        await drain_task

        assert drained == [b"\xaa\xbb", b"\xcc\xdd"]
    finally:
        remove_session("vox-call-1")


@pytest.mark.asyncio
async def test_send_audio_without_session_logs_and_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    import phoneagent.providers.telephony.voximplant as voximplant_module

    async def _fast_wait_for_session(call_id: str, **_: object) -> None:
        return None

    monkeypatch.setattr(voximplant_module, "wait_for_session", _fast_wait_for_session)

    provider = VoximplantTelephonyProvider()

    async def _chunks():
        yield b"\xaa\xbb"

    await provider.send_audio("never-connected", _chunks())  # не должно бросить
