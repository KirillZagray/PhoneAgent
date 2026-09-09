"""Pydantic Settings для конфигурации PhoneAgent.

Все настройки загружаются из переменных окружения или .env файла.
Выбор провайдера через *_PROVIDER переменные.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, HttpUrl, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class TelephonyProvider(StrEnum):
    MOCK = "mock"
    VOXIMPLANT = "voximplant"
    TWILIO = "twilio"
    ASTERISK = "asterisk"


class STTProvider(StrEnum):
    MOCK = "mock"
    WHISPER_API = "whisper_api"
    FASTER_WHISPER = "faster_whisper"


class TTSProvider(StrEnum):
    MOCK = "mock"
    EDGE_TTS = "edge_tts"
    VOICESTUDIO = "voicestudio"
    ELEVENLABS = "elevenlabs"


class LLMProvider(StrEnum):
    MOCK = "mock"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class BookingConnector(StrEnum):
    MOCK = "mock"
    GENERIC_API = "generic_api"
    YCLIENTS = "yclients"
    DIKIDI = "dikidi"
    ALTEGIO = "altegio"


class PhoneAgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ─────────────────────────────────────────────
    app_env: Environment = Environment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_name: str = "PhoneAgent"

    # ── Redis ───────────────────────────────────────────
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[assignment]

    # ── Telephony ───────────────────────────────────────
    telephony_provider: TelephonyProvider = TelephonyProvider.MOCK
    public_webhook_base_url: HttpUrl = Field(default="http://localhost:8000")  # type: ignore[assignment]

    # Voximplant
    voximplant_account_id: str = ""
    voximplant_api_key: str = ""
    voximplant_rule_id: str = ""
    voximplant_scenario_id: str = ""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_caller_id: str = ""

    # Asterisk
    asterisk_host: str = ""
    asterisk_ari_username: str = ""
    asterisk_ari_password: str = ""

    # ── STT ─────────────────────────────────────────────
    stt_provider: STTProvider = STTProvider.MOCK
    openai_api_key: str = ""

    # ── TTS ─────────────────────────────────────────────
    tts_provider: TTSProvider = TTSProvider.MOCK
    voicestudio_url: HttpUrl = Field(default="http://localhost:8000")  # type: ignore[assignment]
    voicestudio_voice_id: str = "ru_female_01"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""

    # ── LLM ─────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.MOCK
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"
    openai_model: str = "gpt-5.4-mini"

    # ── Booking ─────────────────────────────────────────
    booking_connector: BookingConnector = BookingConnector.MOCK
    booking_api_url: str = ""
    booking_api_token: str = ""
    salon_id: str = "demo"

    # ── Voice Pipeline ──────────────────────────────────
    sample_rate: int = 8000
    audio_channels: int = 1
    audio_format: Literal["ulaw", "wav", "opus", "pcm"] = "ulaw"

    # ── Conversation ─────────────────────────────────────
    default_language: str = "ru"
    call_timeout_seconds: int = 300
    max_retries: int = 3


def get_settings() -> PhoneAgentSettings:
    """Возвращает singleton настроек (для удобства импорта)."""
    return PhoneAgentSettings()
