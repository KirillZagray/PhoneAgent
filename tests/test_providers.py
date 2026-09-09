"""Тесты провайдеров (mock-режим)."""

import pytest

from phoneagent.providers.stt.mock import MockSTTProvider
from phoneagent.providers.telephony.mock import MockTelephonyProvider
from phoneagent.providers.tts.mock import MockTTSProvider


@pytest.mark.asyncio
async def test_mock_telephony_make_call() -> None:
    provider = MockTelephonyProvider()
    async with provider:
        ref = await provider.make_call("+79991234567")
        assert ref.provider == "mock"
        assert ref.phone == "+79991234567"
        assert ref.call_id.startswith("mock-")


@pytest.mark.asyncio
async def test_mock_telephony_hangup() -> None:
    provider = MockTelephonyProvider()
    async with provider:
        ref = await provider.make_call("+79991234567")
        await provider.hangup(ref.call_id)
        status = await provider.get_status(ref.call_id)
        assert status.status.value == "completed"


@pytest.mark.asyncio
async def test_mock_stt_inject() -> None:
    stt = MockSTTProvider()
    await stt.connect()
    try:
        stt.inject_text("call1", "Привет, я хочу записаться на стрижку")
        text = await stt.transcribe(b"")
        assert "стрижку" in text
    finally:
        await stt.disconnect()


@pytest.mark.asyncio
async def test_mock_tts_synthesize() -> None:
    tts = MockTTSProvider()
    await tts.connect()
    try:
        audio = await tts.synthesize("Привет")
        assert len(audio) > 0
        assert audio == b"\x00\x00" * 8000  # 1 сек тишины на 8kHz
    finally:
        await tts.disconnect()