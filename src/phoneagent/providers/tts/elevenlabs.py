"""ElevenLabs TTS — премиум-качество.

Просим у API сразу сырой PCM нужной частоты (`output_format=pcm_<rate>`) —
без этого ElevenLabs отдаёт MP3 44.1kHz, который телефония не проиграет.
Для частот, которых нет в их списке, берём 16 kHz и ресемплим.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from phoneagent.config import get_settings
from phoneagent.providers.tts.base import BaseTTSProvider
from phoneagent.utils import get_logger
from phoneagent.utils.audio import pcm_resample

logger = get_logger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"
# https://elevenlabs.io/docs/api-reference/text-to-speech — поддерживаемые pcm_* форматы
PCM_RATES = frozenset({8000, 16000, 22050, 24000, 44100})
FALLBACK_RATE = 16000


class ElevenLabsTTSProvider(BaseTTSProvider):
    """TTS через ElevenLabs API."""

    name = "elevenlabs"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        if not self.settings.elevenlabs_api_key:
            msg = "ELEVENLABS_API_KEY not set"
            raise RuntimeError(msg)
        self._client = httpx.AsyncClient(
            base_url=ELEVENLABS_API_URL,
            headers={"xi-api-key": self.settings.elevenlabs_api_key},
            timeout=60.0,
        )
        logger.info("elevenlabs_connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    @staticmethod
    def _request_rate(sample_rate: int) -> int:
        return sample_rate if sample_rate in PCM_RATES else FALLBACK_RATE

    def _body(self, text: str) -> dict[str, object]:
        return {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
        }

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> bytes:
        if self._client is None:
            msg = "ElevenLabs client not connected"
            raise RuntimeError(msg)
        voice_id = voice or self.settings.elevenlabs_voice_id
        rate = self._request_rate(sample_rate)
        response = await self._client.post(
            f"/text-to-speech/{voice_id}",
            params={"output_format": f"pcm_{rate}"},
            json=self._body(text),
        )
        response.raise_for_status()
        return pcm_resample(response.content, rate, sample_rate)

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[bytes]:
        if self._client is None:
            msg = "ElevenLabs client not connected"
            raise RuntimeError(msg)
        voice_id = voice or self.settings.elevenlabs_voice_id
        rate = self._request_rate(sample_rate)
        async with self._client.stream(
            "POST",
            f"/text-to-speech/{voice_id}/stream",
            params={"output_format": f"pcm_{rate}"},
            json=self._body(text),
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                # Чётное число байт на границе чанка — иначе s16le-семплы «съедут».
                yield pcm_resample(chunk[: len(chunk) & ~1], rate, sample_rate)
