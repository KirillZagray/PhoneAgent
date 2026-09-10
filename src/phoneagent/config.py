"""Pydantic Settings для конфигурации PhoneAgent.

Все настройки загружаются из переменных окружения или .env файла.
Выбор провайдера через *_PROVIDER переменные.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
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
    VOICESTUDIO = "voicestudio"


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

    # ── State store ───────────────────────────────────────
    # "redis" (прод) или "memory" (dev/тесты без поднятого Redis).
    state_store: Literal["redis", "memory"] = "redis"
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[assignment]

    # ── Telephony ───────────────────────────────────────
    telephony_provider: TelephonyProvider = TelephonyProvider.MOCK
    public_webhook_base_url: HttpUrl = Field(default="http://localhost:8000")  # type: ignore[assignment]

    # Voximplant
    # Сценарий привязывается к номеру через Rule в кабинете Voximplant, а не
    # передаётся per-call — отдельной настройки для scenario_id не нужно.
    voximplant_account_id: str = ""
    voximplant_api_key: str = ""
    voximplant_rule_id: str = ""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_caller_id: str = ""

    # Asterisk
    # ARI (control plane) — connect() в AsteriskTelephonyProvider падает,
    # если host/username пустые.
    asterisk_host: str = ""
    asterisk_ari_port: int = 8088
    asterisk_ari_username: str = ""
    asterisk_ari_password: str = ""
    # Имя Stasis-приложения — то же значение должно быть в ARI events
    # WS-подписке и в `app=` параметре Originate (make_call).
    asterisk_stasis_app: str = "phoneagent_bridge"
    # Имя PJSIP-эндпоинта (SIP-транк), через который make_call набирает номер
    # по умолчанию — см. asterisk_conf/pjsip.conf.
    asterisk_trunk_endpoint: str = "mts_exolve"
    # Caller ID для исходящих через транк. Пусто = транк подставит свой дефолтный.
    asterisk_caller_id: str = ""
    # Порт, на котором PhoneAgent слушает AudioSocket-мост (Asterisk
    # подключается к нему из dialplan, см. asterisk_conf/extensions.conf).
    # Хост для bind всегда 0.0.0.0 — задаётся прямо в main.py, не отсюда.
    asterisk_audiosocket_port: int = 40122

    # ── STT ─────────────────────────────────────────────
    stt_provider: STTProvider = STTProvider.MOCK
    openai_api_key: str = ""

    # ── TTS ─────────────────────────────────────────────
    tts_provider: TTSProvider = TTSProvider.MOCK
    # VoiceStudio (https://github.com/debpalash/VoiceStudio) — общий сервер и для
    # tts_provider=voicestudio, и для stt_provider=voicestudio. Дефолтный порт из
    # его докер-образа — 3900.
    voicestudio_url: HttpUrl = Field(default="http://localhost:3900")  # type: ignore[assignment]
    voicestudio_voice_id: str = "ru_female_01"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""

    # ── LLM ─────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.MOCK
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"
    openai_model: str = "gpt-5.4-mini"
    # В живом звонке зависший LLM = тишина в трубке. Дефолт SDK — 10 минут.
    llm_timeout_seconds: float = 15.0
    # Мягкий таймаут: если модель не ответила за это время — агент говорит
    # филлер-фразу ("секунду, уточняю...") и продолжает ждать настоящий
    # ответ (запрос не отменяется). Жёсткий обрыв — llm_timeout_seconds
    # выше, это таймаут самого HTTP-клиента SDK.
    llm_soft_timeout_seconds: float = 4.0
    # Сколько последних реплик отдавать модели (звонок короткий, но кап нужен).
    max_history_messages: int = 40

    # ── Voice Pipeline: защита от эха ──────────────────────
    # Пауза после того, как TTS агента реально ушёл в линию (см.
    # AudioSession.wait_drained), прежде чем начинать слушать клиента.
    # Снижает шанс, что VAD примет хвост эха/реверберации собственного
    # голоса агента на линии за реплику клиента.
    echo_guard_seconds: float = 0.3

    # ── Booking / салон ───────────────────────────────────
    # v1 — один деплой = один салон. salon_id в API принимается, но ничего не выбирает.
    booking_connector: BookingConnector = BookingConnector.MOCK
    booking_api_url: str = ""
    booking_api_token: str = ""
    salon_id: str = "demo"
    salon_name: str = "салон красоты"
    salon_timezone: str = "Europe/Moscow"
    salon_working_hours: str = "10:00-20:00"

    # ── Voice Pipeline ──────────────────────────────────
    sample_rate: int = 8000
    # Размер кадра исходящего аудио к телефонии (см. core/audio_session.paced_frames).
    audio_frame_ms: int = 20

    # ── VAD (Phase B — определение конца реплики клиента) ────
    # ponytail: наивный RMS-порог по амплитуде PCM, не webrtcvad/silero —
    # достаточно для проверки живого пайплайна; апгрейд, если на шумных
    # линиях (плохой GSM) начнёт резать речь слишком рано/поздно.
    vad_energy_threshold: int = 400
    # Сколько ждём, пока клиент вообще начнёт говорить, прежде чем сдаться
    # (ведёт в retry-логику _dialog_loop — "Алло, вы меня слышите?").
    vad_initial_silence_seconds: float = 5.0
    # Пауза такой длины после начала речи считается концом реплики.
    # Было 1.2 — на живой линии резало клиента посреди фразы на обычной
    # запинке/вдохе (см. разбор звонка 2026-09-10: "да, слышу [пауза]
    # запиши меня на 15-е" ловилось как две отдельные реплики).
    vad_silence_seconds: float = 1.6
    # Жёсткий потолок одной реплики — не даёт одному длинному монологу
    # съесть весь call_timeout_seconds.
    vad_max_utterance_seconds: float = 20.0

    # ── Barge-in (клиент начинает говорить, пока агент ещё не договорил) ──
    # ponytail: тот же наивный RMS-порог, что у обычного VAD, без echo
    # cancellation на SIP-транке — риск ложных срабатываний на хвост
    # собственного голоса агента. Смягчено grace-периодом и требованием
    # нескольких подряд громких чанков подряд, а не одного всплеска.
    # Апгрейд, если на реальных звонках барж-ин будет либо не срабатывать
    # на реальную речь, либо ловить эхо: покрутить оба параметра ниже,
    # либо (дороже) добавить AEC на уровне Asterisk/транка.
    barge_in_grace_seconds: float = 0.6
    barge_in_confirm_chunks: int = 3

    # ── Conversation ─────────────────────────────────────
    default_language: str = "ru"
    # Жёсткий потолок длительности одного звонка — цикл диалога прерывается по нему.
    call_timeout_seconds: int = 300
    max_retries: int = 3
    # TTL состояния диалога в хранилище.
    state_ttl_seconds: int = 3600
    # Повторный «Перезвонить» на тот же номер в это окно игнорируется (двойной клик, спам).
    callback_dedupe_seconds: int = 120

    # ── Security ──────────────────────────────────────────
    # Bearer-токен для /call/* и /admin/config. Пусто = открыто (только локальная разработка;
    # в APP_ENV=production пустой токен — отказ запуска).
    api_auth_token: str = ""
    # Общий секрет для входящих /webhooks/*. Та же политика, что и у api_auth_token.
    webhook_secret: str = ""


@lru_cache
def get_settings() -> PhoneAgentSettings:
    """Возвращает singleton настроек (кешируется на процесс)."""
    return PhoneAgentSettings()
