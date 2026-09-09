"""Фабрика провайдеров — собирает нужные реализации по настройкам."""

from __future__ import annotations

from phoneagent.config import PhoneAgentSettings
from phoneagent.providers.stt.base import BaseSTTProvider
from phoneagent.providers.telephony.base import BaseTelephonyProvider
from phoneagent.providers.tts.base import BaseTTSProvider
from phoneagent.utils import get_logger

logger = get_logger(__name__)


def build_telephony_provider(settings: PhoneAgentSettings | None = None) -> BaseTelephonyProvider:
    """Создаёт telephony-провайдер по настройкам."""
    from phoneagent.config import get_settings

    settings = settings or get_settings()
    provider = settings.telephony_provider

    logger.info("building_telephony_provider", provider=provider.value)

    if provider.value == "mock":
        from phoneagent.providers.telephony.mock import MockTelephonyProvider
        return MockTelephonyProvider()

    if provider.value == "voximplant":
        from phoneagent.providers.telephony.voximplant import VoximplantTelephonyProvider
        return VoximplantTelephonyProvider()

    if provider.value == "twilio":
        from phoneagent.providers.telephony.twilio import TwilioTelephonyProvider
        return TwilioTelephonyProvider()

    if provider.value == "asterisk":
        from phoneagent.providers.telephony.asterisk import AsteriskTelephonyProvider
        return AsteriskTelephonyProvider()

    msg = f"Unknown telephony provider: {provider}"
    raise ValueError(msg)


def build_stt_provider(settings: PhoneAgentSettings | None = None) -> BaseSTTProvider:
    """Создаёт STT-провайдер по настройкам."""
    from phoneagent.config import get_settings

    settings = settings or get_settings()
    provider = settings.stt_provider

    logger.info("building_stt_provider", provider=provider.value)

    if provider.value == "mock":
        from phoneagent.providers.stt.mock import MockSTTProvider
        return MockSTTProvider()

    if provider.value == "whisper_api":
        from phoneagent.providers.stt.whisper import WhisperAPIProvider
        return WhisperAPIProvider()

    if provider.value == "faster_whisper":
        from phoneagent.providers.stt.whisper import FasterWhisperProvider
        return FasterWhisperProvider()

    if provider.value == "voicestudio":
        from phoneagent.providers.stt.voicestudio import VoiceStudioSTTProvider
        return VoiceStudioSTTProvider()

    msg = f"Unknown STT provider: {provider}"
    raise ValueError(msg)


def build_tts_provider(settings: PhoneAgentSettings | None = None) -> BaseTTSProvider:
    """Создаёт TTS-провайдер по настройкам."""
    from phoneagent.config import get_settings

    settings = settings or get_settings()
    provider = settings.tts_provider

    logger.info("building_tts_provider", provider=provider.value)

    if provider.value == "mock":
        from phoneagent.providers.tts.mock import MockTTSProvider
        return MockTTSProvider()

    if provider.value == "edge_tts":
        from phoneagent.providers.tts.edge_tts import EdgeTTSProvider
        return EdgeTTSProvider()

    if provider.value == "voicestudio":
        from phoneagent.providers.tts.voicestudio import VoiceStudioTTSProvider
        return VoiceStudioTTSProvider()

    if provider.value == "elevenlabs":
        from phoneagent.providers.tts.elevenlabs import ElevenLabsTTSProvider
        return ElevenLabsTTSProvider()

    msg = f"Unknown TTS provider: {provider}"
    raise ValueError(msg)