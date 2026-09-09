"""Утилиты для аудио.

Контракт пайплайна: между TTS и телефонией ходит сырой PCM 16-bit signed
little-endian, моно, на частоте `settings.sample_rate` (по умолчанию 8000 Hz —
телефонная линия). Всё, что отдаёт провайдер в другом формате (WAV, MP3, другая
частота, стерео), приводится к этому контракту здесь.

`audioop` удалён из stdlib в Python 3.13 — для него в зависимостях `audioop-lts`
(тот же модуль, тот же API).
"""

from __future__ import annotations

import asyncio
import audioop
import io
import wave
from typing import Final

# Поддерживаемые форматы аудио
SUPPORTED_FORMATS: Final[frozenset[str]] = frozenset({"ulaw", "wav", "opus", "pcm"})


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 8000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Конвертирует сырой PCM в WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def wav_to_pcm(wav_data: bytes) -> bytes:
    """Извлекает сырой PCM из WAV (как есть, без ресемплинга)."""
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        return wf.readframes(wf.getnframes())


def pcm_resample(pcm: bytes, src_rate: int, dst_rate: int, *, width: int = 2, channels: int = 1) -> bytes:
    """Ресемплит PCM 16-bit. Без изменений, если частоты совпадают."""
    if src_rate == dst_rate:
        return pcm
    out, _ = audioop.ratecv(pcm, width, channels, src_rate, dst_rate, None)
    return bytes(out)


def wav_to_pcm_resampled(wav_data: bytes, target_rate: int) -> bytes:
    """WAV любой частоты/каналов/ширины -> PCM s16le mono target_rate."""
    with wave.open(io.BytesIO(wav_data), "rb") as wf:
        channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    if width != 2:
        pcm = audioop.lin2lin(pcm, width, 2)
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    return pcm_resample(pcm, rate, target_rate)


async def mp3_to_pcm_ffmpeg(mp3_data: bytes, sample_rate: int) -> bytes:
    """MP3 -> PCM s16le mono sample_rate через ffmpeg (есть в Docker-образе).

    ponytail: subprocess на каждый синтез. Если Edge TTS пойдёт в прод —
    заменить на VoiceStudio/ElevenLabs, которые отдают PCM сами.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-i", "pipe:0",
        "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate), "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(mp3_data)
    if proc.returncode != 0:
        msg = f"ffmpeg failed ({proc.returncode}): {err.decode(errors='replace')[:200]}"
        raise RuntimeError(msg)
    return out


def ulaw_to_pcm(ulaw_data: bytes) -> bytes:
    """Декодирует μ-law (G.711) в PCM 16-bit."""
    return bytes(audioop.ulaw2lin(ulaw_data, 2))


def pcm_to_ulaw(pcm_data: bytes) -> bytes:
    """Кодирует PCM 16-bit в μ-law (G.711)."""
    return bytes(audioop.lin2ulaw(pcm_data, 2))


def is_valid_format(fmt: str) -> bool:
    """Проверяет, поддерживается ли формат."""
    return fmt.lower() in SUPPORTED_FORMATS
