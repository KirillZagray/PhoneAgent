"""Mock-провайдер STT для разработки.

Используется в dev/test — позволяет инжектировать текст через inject_text,
либо возвращает размер входного аудио.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from phoneagent.providers.stt.base import BaseSTTProvider
from phoneagent.utils import get_logger

logger = get_logger(__name__)


class MockSTTProvider(BaseSTTProvider):
    """Имитация STT для разработки."""

    name = "mock"

    def __init__(self) -> None:
        self._connected = False
        self._injected_texts: dict[str, str] = {}

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def inject_text(self, call_id: str, text: str) -> None:
        """Для тестов: зарегистрировать текст, который вернётся при следующем transcribe."""
        self._injected_texts[call_id] = text

    async def transcribe(
        self,
        audio: bytes | AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> str:
        # В моке возвращаем последний инжектированный текст или дефолт
        if self._injected_texts:
            return self._injected_texts.popitem()[1]
        if isinstance(audio, bytes):
            logger.debug("mock_stt_bytes", size=len(audio))
            return f"[mock-stt] {len(audio)} bytes"
        return "[mock-stt] empty"

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[str]:
        await asyncio.sleep(0.05)
        text = "[mock-stt-stream] empty"
        if self._injected_texts:
            text = self._injected_texts.popitem()[1]
        yield text