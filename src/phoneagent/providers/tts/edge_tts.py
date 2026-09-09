"""Edge TTS — бесплатный Microsoft TTS через edge-tts библиотеку.

Не требует API-ключей. Поддерживает русский с хорошими голосами.

Голоса: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator

from phoneagent.providers.tts.base import BaseTTSProvider
from phoneagent.utils import get_logger
from phoneagent.utils.audio import wav_to_pcm

logger = get_logger(__name__)

# Популярные русские голоса
DEFAULT_VOICE = "ru-RU-SvetlanaNeural"


class EdgeTTSProvider(BaseTTSProvider):
    """Microsoft Edge TTS (бесплатный)."""

    name = "edge_tts"

    def __init__(self, default_voice: str = DEFAULT_VOICE) -> None:
        self.default_voice = default_voice
        self._available = False
        self._check_lib()

    def _check_lib(self) -> None:
        try:
            import edge_tts  # noqa: F401  type: ignore[import-not-found]
            self._available = True
        except ImportError:
            self._available = False
            logger.warning("edge_tts_not_installed")

    async def connect(self) -> None:
        if not self._available:
            msg = "edge-tts library not installed. Run: pip install edge-tts"
            raise RuntimeError(msg)

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
        if not self._available:
            msg = "edge-tts not installed"
            raise RuntimeError(msg)
        import edge_tts  # type: ignore[import-not-found]

        voice_name = voice or self.default_voice
        communicate = edge_tts.Communicate(text, voice=voice_name)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await communicate.save(tmp_path)
            with open(tmp_path, "rb") as f:
                mp3_data = f.read()
            # Конвертируем MP3 → WAV → PCM
            pcm = await asyncio.to_thread(self._mp3_to_pcm, mp3_data, sample_rate)
            return pcm
        finally:
            try:
                import os
                os.unlink(tmp_path)
            except OSError:
                pass

    def _mp3_to_pcm(self, mp3_data: bytes, sample_rate: int) -> bytes:
        """Конвертирует MP3 в сырой PCM 16-bit через pydub (или ffmpeg)."""
        try:
            from pydub import AudioSegment  # type: ignore[import-not-found]
            from io import BytesIO
            audio = AudioSegment.from_mp3(BytesIO(mp3_data))
            audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
            return audio.raw_data
        except ImportError:
            logger.warning("pydub_not_available_returning_wav")
            # Fallback: возвращаем MP3 как есть (провайдер не сможет играть без конвертации)
            return mp3_data

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[bytes]:
        # Edge TTS не поддерживает стриминг — отдаём одним чанком
        audio = await self.synthesize(text, voice=voice, language=language, sample_rate=sample_rate)
        yield audio