"""Whisper STT — через OpenAI API или локально (faster-whisper)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from io import BytesIO

import httpx
from openai import AsyncOpenAI

from phoneagent.config import get_settings
from phoneagent.providers.stt.base import BaseSTTProvider
from phoneagent.utils import get_logger
from phoneagent.utils.audio import wav_to_pcm

logger = get_logger(__name__)


class WhisperAPIProvider(BaseSTTProvider):
    """Распознавание речи через OpenAI Whisper API (whisper-1)."""

    name = "whisper_api"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: AsyncOpenAI | None = None

    async def connect(self) -> None:
        if not self.settings.openai_api_key:
            msg = "OPENAI_API_KEY not set for Whisper"
            raise RuntimeError(msg)
        self._client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        logger.info("whisper_api_connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()

    async def _ensure_wav(self, audio: bytes, sample_rate: int) -> bytes:
        """OpenAI Whisper API принимает WAV/MP3 — оборачиваем сырой PCM в WAV."""
        from phoneagent.utils.audio import pcm_to_wav
        try:
            # Если это уже WAV — не оборачиваем
            if audio[:4] == b"RIFF":
                return audio
        except (IndexError, TypeError):
            pass
        return pcm_to_wav(audio, sample_rate=sample_rate)

    async def transcribe(
        self,
        audio: bytes | AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> str:
        if self._client is None:
            msg = "Whisper client not connected"
            raise RuntimeError(msg)

        # Собираем весь поток в байты
        if not isinstance(audio, bytes):
            chunks: list[bytes] = []
            async for chunk in audio:
                chunks.append(chunk)
            audio = b"".join(chunks)

        wav = await self._ensure_wav(audio, sample_rate)
        file_obj = BytesIO(wav)
        file_obj.name = "audio.wav"

        response = await self._client.audio.transcriptions.create(
            model="whisper-1",
            file=file_obj,
            language=language,
        )
        text = response.text
        logger.info("whisper_transcribed", text=text[:80])
        return text

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[str]:
        chunks: list[bytes] = []
        async for chunk in audio_stream:
            chunks.append(chunk)
        if chunks:
            text = await self.transcribe(b"".join(chunks), language=language, sample_rate=sample_rate)
            yield text


class FasterWhisperProvider(BaseSTTProvider):
    """Локальный faster-whisper (без отправки в OpenAI)."""

    name = "faster_whisper"

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8") -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model: object | None = None

    async def connect(self) -> None:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        logger.info("faster_whisper_loaded", model=self.model_size, device=self.device)

    async def disconnect(self) -> None:
        self._model = None

    async def _run_transcribe(self, audio: bytes, language: str, sample_rate: int) -> str:
        if self._model is None:
            msg = "Faster-Whisper not loaded"
            raise RuntimeError(msg)
        pcm = wav_to_pcm(audio) if audio[:4] == b"RIFF" else audio
        # faster_whisper — sync API, оборачиваем в to_thread
        import asyncio
        import numpy as np

        def _transcribe() -> tuple[list[object], str]:
            segments, info = self._model.transcribe(  # type: ignore[attr-defined]
                np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0,
                language=language,
                sample_rate=sample_rate,
            )
            return list(segments), info.language

        segments, _ = await asyncio.to_thread(_transcribe)
        return " ".join(seg.text.strip() for seg in segments)

    async def transcribe(
        self,
        audio: bytes | AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> str:
        if not isinstance(audio, bytes):
            chunks: list[bytes] = []
            async for chunk in audio:
                chunks.append(chunk)
            audio = b"".join(chunks)
        return await self._run_transcribe(audio, language, sample_rate)

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[str]:
        chunks: list[bytes] = []
        async for chunk in audio_stream:
            chunks.append(chunk)
        if chunks:
            text = await self.transcribe(b"".join(chunks), language=language, sample_rate=sample_rate)
            yield text