"""Edge TTS — бесплатный Microsoft TTS через edge-tts библиотеку.

Не требует API-ключей. Отдаёт MP3 — конвертируем в PCM через ffmpeg
(есть в Docker-образе; локально `brew install ffmpeg`). Без ffmpeg — честная
ошибка, а не MP3 под видом PCM.

Голоса: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import edge_tts

from phoneagent.providers.tts.base import BaseTTSProvider
from phoneagent.utils import get_logger
from phoneagent.utils.audio import mp3_to_pcm_ffmpeg

logger = get_logger(__name__)

DEFAULT_VOICE = "ru-RU-SvetlanaNeural"


class EdgeTTSProvider(BaseTTSProvider):
    """Microsoft Edge TTS (бесплатный)."""

    name = "edge_tts"

    def __init__(self, default_voice: str = DEFAULT_VOICE) -> None:
        self.default_voice = default_voice

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
        communicate = edge_tts.Communicate(text, voice=voice or self.default_voice)
        mp3 = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])
        return await mp3_to_pcm_ffmpeg(bytes(mp3), sample_rate)

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[bytes]:
        # MP3 нельзя декодировать почанково без буфера — отдаём одним куском
        yield await self.synthesize(text, voice=voice, language=language, sample_rate=sample_rate)
