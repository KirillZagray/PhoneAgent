"""Mock TTS для разработки."""

from __future__ import annotations

from collections.abc import AsyncIterator

from phoneagent.providers.tts.base import BaseTTSProvider


class MockTTSProvider(BaseTTSProvider):
    """Имитация TTS — возвращает silence-аудио или эхо текста."""

    name = "mock"

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> bytes:
        # 1 секунда тишины в PCM 16-bit 8kHz = 16000 байт
        return b"\x00\x00" * sample_rate

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[bytes]:
        # Возвращаем один чанк silence
        yield b"\x00\x00" * (sample_rate // 4)