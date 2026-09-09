# VoiceStudio Setup (локальный TTS)

VoiceStudio — open-source альтернатива ElevenLabs, работает локально.

## 1. Установите VoiceStudio

См. https://github.com/debpalash/VoiceStudio

```bash
# Самый простой путь — Docker
docker pull debpalash/voicestudio
docker run -d -p 8000:8000 --name voicestudio debpalash/voicestudio

# Или из исходников (требует GPU):
git clone https://github.com/debpalash/VoiceStudio.git
cd VoiceStudio
bun install
bun run desktop
```

## 2. Загрузите модель

При первом запуске VoiceStudio автоматически скачает модель (несколько GB).

Если используете Docker, может потребоваться отдельный шаг для скачивания.

## 3. Проверьте API

VoiceStudio имеет OpenAI-совместимый endpoint:
- `GET /v1/voices` — список голосов
- `POST /v1/audio/speech` — синтез (параметры: input, voice, language, response_format)

```bash
curl http://localhost:8000/v1/voices
```

## 4. Настройте PhoneAgent

```bash
TTS_PROVIDER=voicestudio
VOICESTUDIO_URL=http://localhost:8000
VOICESTUDIO_VOICE_ID=ru_female_01
```

## Преимущества

- ✅ Бесплатно
- ✅ Локально — данные не уходят
- ✅ Русские голоса хорошего качества
- ✅ Open-source

## Недостатки

- ❌ Требует GPU для быстрого синтеза (CPU работает, но медленно)
- ❌ Большие модели (несколько GB)
- ❌ Требует поддержки проекта

## Альтернативы

- **ElevenLabs** — облачный, платный, отличное качество
- **Edge TTS** — бесплатный, через Microsoft (но менее качественный русский)
- **Yandex SpeechKit** — российский, хорошее качество, платный