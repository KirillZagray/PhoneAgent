"""VoiceStudio TTS — локальный open-source клон ElevenLabs.

Интеграция с https://github.com/debpalash/VoiceStudio через его OpenAI-совместимый
HTTP API (по умолчанию слушает http://localhost:3900):
- POST /v1/audio/speech — синтез речи
- GET  /v1/audio/voices — список голосов

STT (распознавание) того же сервера — см. `providers/stt/voicestudio.py`
(VoiceStudio запускает Whisper-семейство ASR — WhisperX/faster-whisper — под
капотом и отдаёт его через тот же OpenAI-совместимый API).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from phoneagent.config import get_settings
from phoneagent.providers.tts.base import BaseTTSProvider
from phoneagent.utils import get_logger

logger = get_logger(__name__)


class VoiceStudioTTSProvider(BaseTTSProvider):
    """TTS через локальный VoiceStudio."""

    name = "voicestudio"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=str(self.settings.voicestudio_url),
            timeout=60.0,
        )
        # Проверяем доступность
        try:
            response = await self._client.get("/v1/audio/voices", timeout=5.0)
            response.raise_for_status()
            logger.info("voicestudio_connected", url=str(self.settings.voicestudio_url))
        except httpx.HTTPError as e:
            logger.warning("voicestudio_unreachable", error=str(e))

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> bytes:
        if self._client is None:
            msg = "VoiceStudio client not connected"
            raise RuntimeError(msg)

        voice_id = voice or self.settings.voicestudio_voice_id

        # VoiceStudio API принимает JSON или multipart
        # Проверяем какой формат поддерживается — упрощённо используем JSON
        response = await self._client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": text,
                "voice": voice_id,
                "language": language,
                "response_format": "wav",
            },
        )
        response.raise_for_status()
        audio_data = response.content
        logger.info("voicestudio_synthesized", bytes=len(audio_data), text_len=len(text))
        return audio_data

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[bytes]:
        if self._client is None:
            msg = "VoiceStudio client not connected"
            raise RuntimeError(msg)

        voice_id = voice or self.settings.voicestudio_voice_id
        async with self._client.stream(
            "POST",
            "/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": text,
                "voice": voice_id,
                "language": language,
                "response_format": "wav",
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                yield chunk