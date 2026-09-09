# Voximplant Setup Guide

Voximplant — российский провайдер телефонии, ближайший аналог Twilio по API.

## 1. Создайте аккаунт

Зарегистрируйтесь на [voximplant.com](https://voximplant.com).

## 2. Купите номер

В разделе "Номера" → купите номер в нужном регионе (для тестов подойдёт номер в Москве).

## 3. Создайте приложение и сценарий

1. Раздел "Приложения" → создайте новое
2. Добавьте VoxEngine Scenario (Node.js):

```javascript
// Scenario: phoneagent-listener
require(Modules.phoneagent);

const call = VoxEngine.callUser(
  VoxEngine.legA(),
  VoxEngine.legB(),
  (events) => {
    if (events.code === 'CALL_CONNECTED') {
      // WebSocket-стрим для AI-агента
      VoxEngine.mediaStream().then(stream => {
        // ... отправляем аудио в PhoneAgent backend
      });
    }
  }
);
```

3. Создайте правило маршрутизации (Rule) для звонков.

## 4. Получите credentials

В разделе "Управление → API-ключи":
- Account ID
- API Key

## 5. Настройте .env

```bash
TELEPHONY_PROVIDER=voximplant
VOXIMPLANT_ACCOUNT_ID=12345
VOXIMPLANT_API_KEY=your-api-key
VOXIMPLANT_RULE_ID=123
VOXIMPLANT_SCENARIO_ID=456
```

## 6. Webhook для событий

VoxEngine Scenario отправляет события звонка на URL `/webhooks/voximplant`.
Убедитесь, что ваш PhoneAgent доступен извне (например через ngrok или reverse proxy).

## 7. Тест

```bash
curl -X POST http://localhost:8000/call/request-callback \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79001234567","salon_id":"demo"}'
```

## Документация

- [Voximplant Documentation](https://voximplant.com/docs/)
- [StartCall API](https://voximplant.com/docs/references/httpapi/StartCall)
- [VoxEngine MediaStreaming](https://voximplant.com/docs/references/voxengine/audiostreaming)