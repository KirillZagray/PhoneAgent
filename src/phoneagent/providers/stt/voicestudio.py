"""VoiceStudio STT — тот же локальный сервер, что и TTS (`tts/voicestudio.py`).

https://github.com/debpalash/VoiceStudio запускает Whisper-семейство ASR
(WhisperX / faster-whisper / MLX Whisper, в зависимости от железа) и отдаёт
его через OpenAI-совместимый эндпоинт:

    POST /v1/audio/transcriptions   (multipart: file, model, language, response_format)

Использует те же VOICESTUDIO_URL, что и TTS-провайдер — VoiceStudio один
процесс на оба направления, отдельная переменная окружения не нужна.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from phoneagent.config import get_settings
from phoneagent.providers.stt.base import BaseSTTProvider
from phoneagent.utils import get_logger
from phoneagent.utils.audio import pcm_to_wav

logger = get_logger(__name__)


class VoiceStudioSTTProvider(BaseSTTProvider):
    """STT через локальный VoiceStudio (Whisper-семейство под капотом)."""

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
            logger.info("voicestudio_stt_connected", url=str(self.settings.voicestudio_url))
        except httpx.HTTPError as e:
            logger.warning("voicestudio_stt_unreachable", error=str(e))

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def transcribe(
        self,
        audio: bytes | AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> str:
        if self._client is None:
            msg = "VoiceStudio client not connected"
            raise RuntimeError(msg)

        if not isinstance(audio, bytes):
            chunks: list[bytes] = []
            async for chunk in audio:
                chunks.append(chunk)
            audio = b"".join(chunks)

        # Сервер ожидает файл (WAV/MP3/...) — сырой PCM оборачиваем в WAV.
        wav = audio if audio[:4] == b"RIFF" else pcm_to_wav(audio, sample_rate=sample_rate)

        response = await self._client.post(
            "/v1/audio/transcriptions",
            data={"model": "whisper-1", "language": language, "response_format": "json"},
            files={"file": ("audio.wav", wav, "audio/wav")},
        )
        response.raise_for_status()
        text = str(response.json().get("text", ""))
        logger.debug("voicestudio_transcribed", text=text[:80])
        return text

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[str]:
        # ponytail: VoiceStudio поддерживает WS /v1/audio/transcriptions/stream с
        # частичными результатами — здесь пока копим весь чанк и шлём одним запросом.
        # Апгрейд на WS-стриминг — вместе с реализацией send_audio у реальных
        # telephony-провайдеров (см. supports_realtime_audio).
        chunks: list[bytes] = []
        async for chunk in audio_stream:
            chunks.append(chunk)
        if chunks:
            text = await self.transcribe(b"".join(chunks), language=language, sample_rate=sample_rate)
            yield text


__all__ = ["VoiceStudioSTTProvider"]
