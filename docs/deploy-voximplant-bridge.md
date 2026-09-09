# Деплой Phase A (echo-мост) на внешний сервер

Инструкция для агента/оператора, который поднимает PhoneAgent на сервере с
доменом и HTTPS, чтобы протестировать аудио-мост с реальным звонком через
Voximplant. Контекст и почему именно так — в
[docs/superpowers/specs/2026-09-09-voximplant-audio-bridge-design.md](superpowers/specs/2026-09-09-voximplant-audio-bridge-design.md).

Что получится в конце: набираете тестовый номер, говорите что-то — слышите
своё же эхо. Это Phase A — проверяем только транспорт (VoxEngine ↔ наш сервер),
без STT/LLM/TTS.

## 0. Что нужно заранее

- Сервер с публичным доменом и валидным HTTPS-сертификатом. **Важно**:
  `wss://` в VoxEngine принимает только домен, не голый IP — без домена этот
  шаг вообще не заработает.
- Docker + Docker Compose на сервере.
- Аккаунт Voximplant с `account_id` + `api_key` (Настройки → API), и
  арендованный/верифицированный номер (нужен как Caller ID для исходящих
  звонков — тестовые номера Voximplant для исходящих не годятся).
- Reverse proxy с TLS перед контейнером (nginx/Caddy/Traefik — что уже есть
  на сервере). `docker-compose.yml` в репозитории поднимает только HTTP на
  8000 внутри compose-сети, TLS-терминацию сам не делает.

## 1. Клонировать и настроить

```bash
git clone https://github.com/KirillZagray/PhoneAgent.git
cd PhoneAgent
cp .env.example .env
```

Отредактировать `.env`:

```bash
APP_ENV=production
STATE_STORE=redis          # уже задан принудительно в docker-compose, но пусть будет явно

TELEPHONY_PROVIDER=voximplant
PUBLIC_WEBHOOK_BASE_URL=https://ваш-домен.example

VOXIMPLANT_ACCOUNT_ID=...
VOXIMPLANT_API_KEY=...
VOXIMPLANT_RULE_ID=...      # заполнить после шага 3 — правило ещё не создано

# Phase A не трогает STT/LLM/TTS/booking — оставить mock, чтобы не тянуть
# лишние ключи для одной только проверки моста.
STT_PROVIDER=mock
TTS_PROVIDER=mock
LLM_PROVIDER=mock
BOOKING_CONNECTOR=mock

# ОБЯЗАТЕЛЬНО оба — APP_ENV=production без них не стартует (fail-closed).
API_AUTH_TOKEN=<сгенерируйте, например: openssl rand -hex 32>
WEBHOOK_SECRET=<сгенерируйте отдельно: openssl rand -hex 32>
```

## 2. Поднять контейнеры

```bash
docker compose up -d --build
docker compose logs -f phoneagent   # ждём "phoneagent_starting"
```

Настройте reverse proxy на сервере так, чтобы `https://ваш-домен.example/*`
проксировался на `http://127.0.0.1:8000` (порт уже пробрасывается наружу в
`docker-compose.yml`) — включая WebSocket-апгрейд для пути `/ws/voxengine/*`
(в nginx это `proxy_set_header Upgrade $http_upgrade;` + `Connection "upgrade"`,
в Caddy/Traefik апгрейд идёт из коробки).

Проверка, что сервис живой и наружу:

```bash
curl https://ваш-домен.example/admin/health
# {"status":"ok","service":"phoneagent"}
```

## 3. Настроить Voximplant

В кабинете [manage.voximplant.com](https://manage.voximplant.com):

1. **Applications** → создать приложение (или использовать существующее).
2. **Scenarios** → New scenario → вставить содержимое
   [`voxengine/phoneagent_scenario.js`](../voxengine/phoneagent_scenario.js) из
   этого репозитория. Перед сохранением заполнить три константы вверху файла:
   - `BACKEND_WSS_HOST` — ваш домен без `https://` и без пути (например `phoneagent.example.com`).
   - `WEBHOOK_SECRET` — то же значение, что `WEBHOOK_SECRET` в `.env`.
   - `CALLER_ID` — арендованный номер с этого аккаунта (см. п. 0).
3. **Routing** → New rule → привязать созданный сценарий, шаблон номера
   оставить дефолтным (`.*`).
4. Скопировать **Rule ID** — это значение для `VOXIMPLANT_RULE_ID` в `.env`.
   Обновить `.env` и перезапустить:
   ```bash
   docker compose up -d --force-recreate phoneagent
   ```

## 4. Тестовый звонок

**Важно:** для Phase A звоните напрямую через Voximplant Management API, **не**
через `/call/request-callback`. Этот эндпоинт запускает полный `Orchestrator`,
который тут же попробует сказать приветствие через `send_audio()` — а он в
Phase A намеренно `NotImplementedError` (аудио идёт через мост, не через
Orchestrator, см. spec) — и сам повесит трубку через долю секунды, независимо
от того, что сценарий уже ведёт звонок. `/call/request-callback` заработает
для Voximplant только в Phase B.

```bash
curl "https://api.voximplant.com/platform_api/StartScenarios" \
  -d "account_id=$VOXIMPLANT_ACCOUNT_ID" \
  -d "api_key=$VOXIMPLANT_API_KEY" \
  -d "rule_id=$VOXIMPLANT_RULE_ID" \
  -d "script_custom_data=test-echo-1|+7<ваш_личный_номер>"
```

`test-echo-1` — id звонка для этого теста, любая строка без `|` внутри
(сценарий берёт по нему адрес `wss://.../ws/voxengine/test-echo-1`).
Ответ должен содержать `call_session_history_id` и
`media_session_access_secure_url` — значит `StartScenarios` принял запрос,
Voximplant запускает сценарий. На указанный номер должен поступить звонок.
Скажите что-нибудь после соединения — должны услышать своё же эхо с
небольшой задержкой.

Смотрите логи параллельно:

```bash
docker compose logs -f phoneagent | grep voxengine_bridge
```

Ожидаемая последовательность: `voxengine_bridge_connected` →
`voxengine_bridge_start` → серия `media`-событий (в логи не пишутся по одному,
это нормально) → `voxengine_bridge_stop`/`voxengine_bridge_disconnected` после
того как повесите трубку.

## Диагностика

| Симптом | Вероятная причина |
|---|---|
| `curl /admin/health` не отвечает | reverse proxy не проксирует на 8000, или контейнер не поднялся — `docker compose ps` |
| `POST /call/request-callback` → 400 | `TELEPHONY_PROVIDER` не `voximplant`, или опечатка в проверке `supports_realtime_audio` — mock всегда 200 |
| `POST /call/request-callback` → 401 | не тот `Authorization: Bearer` |
| Звонок не приходит вообще | `VOXIMPLANT_RULE_ID` не тот/сценарий не привязан к правилу; смотрите Call History в кабинете Voximplant — там же текст ошибки `StartScenarios`, если он был |
| Звонок приходит, сразу сбрасывается | `CALLER_ID` в сценарии — не верифицированный/не арендованный номер (Voximplant отклоняет `callPSTN` с таким caller ID) |
| Соединение есть, эха нет | сценарий не достучался до `wss://BACKEND_WSS_HOST/ws/voxengine/...` — проверьте `BACKEND_WSS_HOST` без `https://`/пути, TLS на прокси, и что прокси реально апгрейдит WebSocket на этом пути |
| В логах `voxengine_bridge_disconnected` сразу после connect, код 4401 | `WEBHOOK_SECRET` в сценарии не совпадает с `.env` |

## После Phase A

Если эхо работает — сообщите об этом, чтобы перейти к Phase B (реальные
STT/LLM/TTS вместо эха). Правки Phase B будут в тех же файлах
(`media_ws.py`, `voxengine/phoneagent_scenario.js`) — обновлять их с сервера
достаточно через `git pull && docker compose up -d --build`.
