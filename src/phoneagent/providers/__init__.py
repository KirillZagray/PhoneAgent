"""Провайдеры — подключаемые реализации через единые интерфейсы."""

from phoneagent.providers.factory import (
    build_stt_provider,
    build_telephony_provider,
    build_tts_provider,
)

__all__ = [
    "build_stt_provider",
    "build_telephony_provider",
    "build_tts_provider",
]
