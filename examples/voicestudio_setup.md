# VoiceStudio Setup — локальные TTS **и** STT одним сервером

[VoiceStudio](https://github.com/debpalash/VoiceStudio) — open-source альтернатива
ElevenLabs, работает полностью локально. Для PhoneAgent важно, что это **один процесс
на оба направления**: синтез речи (TTS) и распознавание (STT, движки Whisper-семейства —
WhisperX / faster-whisper / MLX Whisper под капотом). Отдельный Whisper-сервис не нужен.

## 1. Запуск

```bash
# Docker (рекомендуется). Порт по умолчанию — 3900.
docker run -d -p 127.0.0.1:3900:3900 \
  -v omnivoice-data:/app/omnivoice_data \
  --name voicestudio palashdeb/omnivoice-studio:stable
```

Есть также desktop-сборки (macOS 13.3+, Windows, Linux) — см. README проекта.
Минимум 8 GB RAM, 10 GB диска; GPU (CUDA / Apple MPS / ROCm) сильно ускоряет.

## 2. Модели и лицензии (важно для коммерческого использования)

При первом запуске VoiceStudio скачивает модели (несколько GB). В каталоге 16 TTS-
и 11 ASR-движков. **Лицензии у моделей разные**, и многие популярные TTS-модели —
**CC-BY-NC (некоммерческие)**. Сам VoiceStudio — AGPL-3.0; для PhoneAgent это не
проблема, пока он остаётся отдельным сервисом за HTTP, но модели внутри него — ваша
ответственность.

Перед тем как предлагать PhoneAgent салонам за деньги:

1. В Model Catalogue VoiceStudio выберите TTS- и ASR-движки с коммерчески допустимой
   лицензией (Apache-2.0 / MIT).
2. Зафиксируйте выбор в `.env` (`VOICESTUDIO_VOICE_ID`) и в этом файле.

## 3. Проверка API

VoiceStudio поднимает OpenAI-совместимый API на `http://localhost:3900`:

| Метод | Путь | Что делает |
|---|---|---|
| `GET` | `/v1/audio/voices` | список голосовых профилей и движков |
| `POST` | `/v1/audio/speech` | TTS (`model`, `input`, `voice`, `response_format`: wav/mp3/pcm/…) |
| `POST` | `/v1/audio/transcriptions` | STT (multipart `file`, `model`, `language`, `response_format`) |
| `WS` | `/v1/audio/transcriptions/stream` | live-транскрипция (partial/final события) |

```bash
curl http://localhost:3900/v1/audio/voices

curl -X POST http://localhost:3900/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","input":"Здравствуйте, это салон красоты","voice":"<profile-id>","response_format":"wav"}' \
  --output test.wav

curl -X POST http://localhost:3900/v1/audio/transcriptions \
  -F file=@test.wav -F model=whisper-1 -F language=ru
```

## 4. Настройка PhoneAgent

```bash
TTS_PROVIDER=voicestudio
STT_PROVIDER=voicestudio
VOICESTUDIO_URL=http://localhost:3900
VOICESTUDIO_VOICE_ID=<profile-id из /v1/audio/voices>
```

PhoneAgent запрашивает WAV и сам приводит его к PCM 16-bit mono `SAMPLE_RATE`
(8000 Hz для телефонии). Для STT отправляет WAV на `/v1/audio/transcriptions`.
Live-стриминг через WS появится вместе с реальным telephony-провайдером (Roadmap).

## Преимущества / ограничения

- ✅ Бесплатно, локально — аудио клиентов не уходит третьим лицам (152-ФЗ)
- ✅ TTS и STT одной установкой
- ❌ Нужно железо (GPU желателен), первый запуск качает гигабайты
- ❌ Лицензии моделей — проверять (см. п. 2)

## Альтернативы

- **ElevenLabs** — облачный, платный, отличное качество; PhoneAgent просит у него
  сразу `pcm_8000`.
- **Edge TTS** — бесплатный (Microsoft), средний русский; нужен `ffmpeg`.
- **OpenAI Whisper API** — `STT_PROVIDER=whisper_api`, облако.
- **faster-whisper локально** — `STT_PROVIDER=faster_whisper`, `uv sync --extra local-stt`.
