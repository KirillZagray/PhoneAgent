"""ElevenLabs TTS — премиум-качество."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from phoneagent.config import get_settings
from phoneagent.providers.tts.base import BaseTTSProvider
from phoneagent.utils import get_logger

logger = get_logger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"


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
        response = await self._client.post(
            f"/text-to-speech/{voice_id}",
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
            },
        )
        response.raise_for_status()
        return response.content

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
        async with self._client.stream(
            "POST",
            f"/text-to-speech/{voice_id}/stream",
            json={"text": text, "model_id": "eleven_multilingual_v2"},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                yield chunk