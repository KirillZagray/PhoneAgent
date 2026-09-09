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

### 2. Запустить с моками (без реальных звонков)

```bash
uv run python -m phoneagent.main
# в другом терминале:
curl -X POST http://localhost:8000/call/request-callback \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","salon_id":"demo"}'
```

Откроется WebSocket консоль, где можно ввести текстом что «говорит клиент» и услышать/увидеть ответы ассистента.

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
│   │   ├── trigger.py           # POST /call/request-callback
│   │   ├── webhooks.py          # POST /webhooks/{provider}
│   │   └── admin.py             # GET /calls, /health, /config
│   ├── core/
│   │   ├── orchestrator.py      # Главный цикл: STT→LLM→TTS→Telephony
│   │   ├── conversation.py      # FSM (greeting→service→date→time→master→confirm)
│   │   ├── agent.py             # LLM agent с tool calling
│   │   └── prompts.py           # System prompts (multi-language)
│   ├── providers/
│   │   ├── telephony/
│   │   │   ├── base.py          # ABC
│   │   │   ├── mock.py          # для dev (текстовая консоль)
│   │   │   ├── voximplant.py    # РФ
│   │   │   ├── twilio.py
│   │   │   └── asterisk.py      # self-hosted
│   │   ├── stt/
│   │   │   ├── base.py
│   │   │   ├── whisper_api.py
│   │   │   └── faster_whisper.py
│   │   └── tts/
│   │       ├── base.py
│   │       ├── edge_tts.py      # бесплатный
│   │       ├── voicestudio.py
│   │       └── elevenlabs.py
│   ├── connectors/
│   │   ├── base.py
│   │   ├── generic_api.py       # универсальный REST клиент
│   │   ├── yclients.py
│   │   ├── dikidi.py
│   │   └── altegio.py
│   ├── models/
│   │   ├── call.py
│   │   ├── booking.py
│   │   └── conversation.py
│   └── utils/
│       ├── audio.py
│       └── logging.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── examples/
│   ├── voximplant_setup.md
│   ├── twilio_setup.md
│   ├── voicestudio_setup.md
│   └── salon_openapi.yaml       # пример ТЗ для интеграции с системой салона
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## 🔧 Интеграция с системой записи салона

Самый простой способ — `GenericAPIConnector` + OpenAPI-спека вашей системы. Если у салона **YClients, Dikidi или Altegio** — есть готовые адаптеры.

Пример ТЗ для салона в `examples/salon_openapi.yaml`:

```yaml
endpoints:
  list_services: GET /services
  list_masters: GET /masters?service_id={id}
  list_slots: GET /slots?master_id={id}&date={date}
  create_booking: POST /bookings
```

## 🛣 Roadmap

| # | Этап | Статус |
|---|------|--------|
| 1 | Скелет + mock-провайдеры + FSM | ✅ |
| 2 | Voximplant + WebSocket-стриминг | ⏳ |
| 3 | Edge TTS + VoiceStudio провайдеры | ⏳ |
| 4 | Whisper STT (API + local) | ⏳ |
| 5 | LLM agent с tool use (Anthropic) | ✅ |
| 6 | GenericAPI booking connector | ✅ |
| 7 | YClients / Dikidi / Altegio адаптеры | ⏳ |
| 8 | Twilio / Asterisk провайдеры | ⏳ |
| 9 | Production hardening (Langfuse, retries) | ⏳ |

## 📄 Лицензия

MIT
