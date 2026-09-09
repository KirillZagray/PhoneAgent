"""VoiceStudio TTS — локальный open-source клон ElevenLabs.

Интеграция с https://github.com/debpalash/VoiceStudio через его OpenAI-совместимый
HTTP API (по умолчанию слушает http://localhost:3900):
- POST /v1/audio/speech — синтез речи
- GET  /v1/audio/voices — список голосов

STT (распознавание) того же сервера — см. `providers/stt/voicestudio.py`
(VoiceStudio запускает Whisper-семейство ASR — WhisperX/faster-whisper — под
капотом и отдаёт его через тот же OpenAI-совместимый API).

Запрашиваем WAV (частота у движков VoiceStudio разная — 22.05/24/44.1 kHz) и
приводим к контракту пайплайна: PCM s16le mono `sample_rate`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx

from phoneagent.config import get_settings
from phoneagent.providers.tts.base import BaseTTSProvider
from phoneagent.utils import get_logger
from phoneagent.utils.audio import wav_to_pcm_resampled

logger = get_logger(__name__)

STREAM_CHUNK = 4096


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

        response = await self._client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": text,
                "voice": voice or self.settings.voicestudio_voice_id,
                "language": language,
                "response_format": "wav",
            },
        )
        response.raise_for_status()
        pcm = await asyncio.to_thread(wav_to_pcm_resampled, response.content, sample_rate)
        logger.debug("voicestudio_synthesized", bytes=len(pcm), text_len=len(text))
        return pcm

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[bytes]:
        # ponytail: WAV-стрим нельзя ресемплить почанково без разбора заголовка —
        # синтезируем целиком и режем. Настоящий стриминг — вместе с send_audio
        # у реального telephony-провайдера (там же и pcm-формат без заголовка).
        pcm = await self.synthesize(text, voice=voice, language=language, sample_rate=sample_rate)
        for i in range(0, len(pcm), STREAM_CHUNK):
            yield pcm[i : i + STREAM_CHUNK]
