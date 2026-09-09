# PhoneAgent

**Универсальный микросервис для автоматизации телефонных записей с AI-ассистентом.**

Клиент нажимает «Перезвонить» на сайте салона/клиники/парикмахерской → AI-ассистент звонит ему и ведёт голосовой диалог для записи на услугу.

## ✨ Что это решает

- 80% звонков в салон — это «записаться на стрижку в четверг после 18». Это автоматизируется.
- Администратор освобождается для реальных клиентов
- Запись работает 24/7, без выходных и обедов

## 🏗 Архитектура

```
┌──────────────────────────────────────────────────────────────┐
│                   Trigger (Webhook / API)                    │
│           "Клиент нажал Перезвонить для записи"              │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│          Orchestrator  (FastAPI + FSM диалога)               │
│   - LLM-агент с tool calling                                 │
│   - ConversationState                                         │
│   - Retry policy, timeout, escalation                        │
└──────┬───────────────────┬────────────────────┬───────────────┘
       ▼                   ▼                    ▼
┌──────────────┐   ┌──────────────────┐  ┌──────────────────┐
│  Telephony   │   │  Voice Pipeline  │  │    Booking       │
│  ────────    │   │  ──────────────  │  │    Connector     │
│  Voximplant  │   │  STT (Whisper)   │  │    ──────────    │
│  Asterisk    │   │  TTS (VoiceStu.) │  │    YClients      │
│  Mock (dev)  │   │  Edge TTS (free) │  │    Dikidi        │
│  Twilio      │   │  ElevenLabs      │  │    Altegio       │
└──────────────┘   └──────────────────┘  │    Generic API   │
                                         └──────────────────┘
```

**VoiceStudio** ([debpalash/VoiceStudio](https://github.com/debpalash/VoiceStudio)) — один локальный
сервер и для TTS, и для STT: OpenAI-совместимый API (`POST /v1/audio/speech` для синтеза,
`POST /v1/audio/transcriptions` для распознавания — под капотом Whisper-семейство ASR,
WhisperX/faster-whisper). Достаточно поднять один контейнер и выставить `TTS_PROVIDER=voicestudio`
+ `STT_PROVIDER=voicestudio` с общим `VOICESTUDIO_URL` — отдельный Whisper-провайдер не нужен.

## 🎯 Ключевая идея — провайдеры подключаются через единый интерфейс

```python
class BaseTelephonyProvider(ABC):
    async def make_call(self, to: str, webhook_url: str) -> CallRef: ...
    async def stream_audio(self, call_id: str, audio: AsyncIterator[bytes]) -> None: ...
    async def hangup(self, call_id: str) -> None: ...

class BaseSTT(ABC):
    async def transcribe_stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]: ...

class BaseTTS(ABC):
    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]: ...

class BaseBookingConnector(ABC):
    async def list_services(self) -> list[Service]: ...
    async def list_masters(self, service_id: str, date: date) -> list[Master]: ...
    async def list_slots(self, master_id: str, date: date) -> list[Slot]: ...
    async def create_booking(self, slot_id: str, client_phone: str) -> Booking: ...
```

Переключение провайдера = одна строка в `.env`.

## 📦 Стек

- **Backend:** Python 3.11+, FastAPI, Pydantic v2, `uv`
- **Telephony:** Voximplant (РФ), Twilio, Asterisk, Mock
- **STT:** Whisper API / faster-whisper (локально)
- **TTS:** Edge TTS (dev), VoiceStudio (прод, русский), ElevenLabs
- **LLM:** Anthropic Claude / OpenAI с tool use
- **State:** Redis для сессий диалогов
- **Booking:** GenericAPI + адаптеры YClients / Dikidi / Altegio

## 🚀 Быстрый старт

### 1. Клонировать и установить

```bash
git clone https://github.com/KirillZagray/PhoneAgent.git
cd PhoneAgent
uv sync
cp .env.example .env
# отредактировать .env под свои провайдеры
```

### 2. Запустить с моками (без реальных звонков, без Redis)

По умолчанию `.env.example` уже настроен на моки и `STATE_STORE=memory` — реальные
звонки, Redis, LLM/telephony-ключи для этого шага не нужны.

```bash
uv run python -m phoneagent.main
# в другом терминале — текстовый диалог с FSM (тот же путь, что и голосовой звонок,
# только без telephony/STT/TTS):
curl -X POST http://localhost:8000/call/text -H "Content-Type: application/json" \
  -d '{"text":"Здравствуйте"}'
# ответ содержит call_id — передавайте его дальше, чтобы продолжить тот же диалог:
curl -X POST http://localhost:8000/call/text -H "Content-Type: application/json" \
  -d '{"text":"Хочу стрижку","call_id":"<call_id из прошлого ответа>"}'
```

`POST /call/request-callback` (реальный дозвон) намеренно откажет с 400, пока
`TELEPHONY_PROVIDER` не `mock` — реалтайм-аудио стриминг для Voximplant/Twilio/Asterisk
ещё не реализован (см. Roadmap), и звонок с ними просто дозвонится и сразу оборвётся.

### 3. Подключить реальные провайдеры

Заполнить `.env`:

```bash
TELEPHONY_PROVIDER=voximplant
VOXIMPLANT_ACCOUNT_ID=...
VOXIMPLANT_API_KEY=***
VOXIMPLANT_RULE_ID=...

STT_PROVIDER=whisper_api
OPENAI_API_KEY=sk-...

TTS_PROVIDER=voicestudio
VOICESTUDIO_URL=http://localhost:8000
VOICESTUDIO_VOICE_ID=ru_female_01

LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

BOOKING_CONNECTOR=generic_api
BOOKING_API_URL=https://yclients.com/api/v1
BOOKING_API_TOKEN=***
```

## 📁 Структура проекта

```
phoneagent/
├── src/phoneagent/
│   ├── main.py                  # FastAPI app
│   ├── config.py                # Pydantic Settings
│   ├── api/
│   │   ├── trigger.py           # POST /call/request-callback, /call/text
│   │   ├── webhooks.py          # POST /webhooks/{provider}
│   │   ├── admin.py             # GET /admin/health, /config, /version
│   │   └── security.py          # Bearer-токен / webhook-secret зависимости
│   ├── core/
│   │   ├── orchestrator.py      # Главный цикл: STT→LLM→TTS→Telephony
│   │   ├── agent.py             # LLM agent с tool calling (Anthropic/OpenAI/mock)
│   │   ├── state_store.py       # Redis / in-memory хранилище состояний
│   │   └── prompts.py           # System prompts (multi-language)
│   ├── providers/
│   │   ├── telephony/
│   │   │   ├── base.py          # ABC (+ supports_realtime_audio флаг)
│   │   │   ├── mock.py          # для dev (единственный полностью рабочий)
│   │   │   ├── voximplant.py    # РФ — заготовка, без send_audio
│   │   │   ├── twilio.py        # заготовка, без send_audio
│   │   │   └── asterisk.py      # self-hosted — заготовка
│   │   ├── stt/
│   │   │   ├── base.py
│   │   │   ├── mock.py
│   │   │   ├── whisper.py       # WhisperAPIProvider (OpenAI) + FasterWhisperProvider (локально)
│   │   │   └── voicestudio.py   # тот же сервер, что и tts/voicestudio.py
│   │   └── tts/
│   │       ├── base.py
│   │       ├── mock.py
│   │       ├── edge_tts.py      # бесплатный
│   │       ├── voicestudio.py
│   │       └── elevenlabs.py
│   ├── connectors/
│   │   ├── base.py
│   │   ├── mock.py
│   │   ├── generic_api.py       # универсальный REST клиент
│   │   └── factory.py           # yclients/dikidi/altegio пока raise NotImplementedError
│   ├── models/
│   │   ├── call.py
│   │   ├── booking.py
│   │   └── conversation.py
│   └── utils/
│       ├── audio.py
│       └── __init__.py          # логирование (structlog) тут же
├── tests/
├── examples/
│   ├── voximplant_setup.md
│   ├── voicestudio_setup.md
│   └── salon_openapi.md         # пример ТЗ для интеграции с системой салона
├── .github/workflows/ci.yml     # ruff + mypy + pytest
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## 🔧 Интеграция с системой записи салона

Самый простой способ — `GenericAPIConnector` + OpenAPI-спека вашей системы. Адаптеры для
**YClients, Dikidi, Altegio** — в планах (см. Roadmap), пока не реализованы.

Пример ТЗ для салона в `examples/salon_openapi.md`:

```yaml
endpoints:
  list_services: GET /services
  list_masters: GET /masters?service_id={id}
  list_slots: GET /slots?master_id={id}&date={date}
  create_booking: POST /bookings
```

## 🔒 Безопасность

`/call/*` и `/webhooks/*` без настройки открыты всем — годится только для локальной
разработки. Перед деплоем задайте в `.env`:

- `API_AUTH_TOKEN` — сайт салона шлёт его как `Authorization: Bearer <token>`
  в `/call/request-callback` и `/call/text`.
- `WEBHOOK_SECRET` — провайдер (или ваш прокси перед ним) должен слать заголовок
  `X-Webhook-Secret` с этим значением на `/webhooks/*`.

Это общий shared-secret, а не честная per-provider проверка подписи (Twilio своя
HMAC-схема, у Voximplant/Asterisk — своя). Апгрейд на нативную проверку подписи —
следующий шаг, когда появится реальная интеграция с конкретным провайдером.

`/docs` и `/redoc` (Swagger) отключены везде, кроме `APP_ENV=development`.

## 🛣 Roadmap

| # | Этап | Статус |
|---|------|--------|
| 1 | Скелет + mock-провайдеры + FSM | ✅ |
| 2 | Voximplant + WebSocket-стриминг | ⏳ |
| 3 | Edge TTS + VoiceStudio провайдеры (TTS) | ✅ |
| 3b | VoiceStudio STT (Whisper через тот же сервер) | ✅ |
| 4 | Whisper STT — OpenAI API / faster-whisper локально | ✅ |
| 5 | LLM agent с tool use (Anthropic/OpenAI) | ✅ |
| 6 | GenericAPI booking connector | ✅ |
| 7 | YClients / Dikidi / Altegio адаптеры | ⏳ |
| 8 | Twilio / Asterisk — реалтайм send_audio (WS/Media Streams) | ⏳ |
| 9 | Production hardening: auth на /call и /webhooks, CI, retries (generic_api) | ✅ |
| 10 | Per-provider проверка подписи вебхуков (Twilio HMAC и т.д.) | ⏳ |
| 11 | Langfuse / observability | ⏳ |

Статусы 2/8 (реальная телефония) намеренно заблокированы на уровне кода:
`Orchestrator.handle_callback` отказывает с ошибкой, если у провайдера
`supports_realtime_audio = False`, — чтобы не дозваниваться и не ронять
реальный звонок клиента впустую.

## 📄 Лицензия

MIT
