"""Утилиты для аудио."""

from __future__ import annotations

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
    """Извлекает сырой PCM из WAV."""
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        return wf.readframes(wf.getnframes())


def ulaw_to_pcm(ulaw_data: bytes) -> bytes:
    """Декодирует μ-law в PCM 16-bit.

    Простая реализация для тестов. В проде лучше использовать `audioop` или `pylibulaw`.
    """
    # μ-law декодирование таблица (стандарт G.711)
    BIAS = 0x84
    CLIP = 8159

    SIGN_BIT = 0x80
    QUANT_MASK = 0x0F
    SEG_SHIFT = 4
    SEG_MASK = 0x70
    BYTE_MASK = 0xFF

    out = bytearray(len(ulaw_data) * 2)
    for i, b in enumerate(ulaw_data):
        ub = (~b) & BYTE_MASK
        sign = ub & SIGN_BIT
        ub <<= 3  # shift into 13-bit linear position
        seg = (ub & SEG_MASK) >> SEG_SHIFT
        if seg:
            ub = (ub & QUANT_MASK) << (seg + 3)
        else:
            ub = (ub & QUANT_MASK) << 4
        ub -= BIAS
        if sign:
            ub = -ub
        ub = max(-CLIP, min(CLIP, ub))
        out[i * 2] = ub & BYTE_MASK
        out[i * 2 + 1] = (ub >> 8) & BYTE_MASK
    return bytes(out)


def pcm_to_ulaw(pcm_data: bytes) -> bytes:
    """Кодирует PCM 16-bit в μ-law (G.711)."""
    import audioop  # type: ignore[import-not-found]
    return audioop.lin2ulaw(pcm_data, 2)


def is_valid_format(fmt: str) -> bool:
    """Проверяет, поддерживается ли формат."""
    return fmt.lower() in SUPPORTED_FORMATS
