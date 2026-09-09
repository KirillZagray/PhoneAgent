/**
 * PhoneAgent — VoxEngine-сценарий аудио-моста, Phase A (echo).
 *
 * Как задеплоить: см. docs/deploy-voximplant-bridge.md в этом репозитории —
 * там пошагово, куда вставить этот файл и что заполнить руками в кабинете
 * Voximplant. Здесь только сам скрипт.
 *
 * Что делает:
 *   1. Читает call_id и номер клиента из customData (мы передаём их через
 *      StartScenarios.script_custom_data как "call_id|phone" — см.
 *      src/phoneagent/providers/telephony/voximplant.py).
 *   2. Сама набирает номер через VoxEngine.callPSTN (StartScenarios не звонит
 *      сама — это делает сценарий).
 *   3. Как только звонок подключился — открывает WebSocket на наш бэкенд
 *      (/ws/voxengine/{call_id}) и связывает аудио звонка с сокетом в обе
 *      стороны через sendMediaTo. Формат по умолчанию PCM16_8KHZ — совпадает
 *      с PhoneAgent, конвертация не нужна.
 *   4. AppEvents.HttpRequest — так наш бэкенд просит завершить звонок (см.
 *      VoximplantTelephonyProvider.hangup): HTTP-запрос на
 *      media_session_access_secure_url приходит сюда этим событием. Сессия
 *      после такого запроса завершается автоматически платформой — обработчик
 *      нужен только для лога, не для самого завершения.
 *
 * Протокол WS-моста (JSON, не бинарный!) и обоснование PCM16_8KHZ —
 * docs/superpowers/specs/2026-09-09-voximplant-audio-bridge-design.md.
 *
 * ВАЖНО — заполнить перед вставкой в кабинет Voximplant:
 */
const BACKEND_WSS_HOST = "REPLACE_ME.example.com"; // без wss://, без пути — просто домен
const WEBHOOK_SECRET = "REPLACE_ME"; // то же значение, что WEBHOOK_SECRET в .env бэкенда
const CALLER_ID = "REPLACE_ME"; // номер, арендованный на этом Voximplant-аккаунте

VoxEngine.addEventListener(AppEvents.Started, function () {
  const raw = VoxEngine.customData() || "";
  const sep = raw.indexOf("|");
  const callId = sep === -1 ? raw : raw.slice(0, sep);
  const phone = sep === -1 ? "" : raw.slice(sep + 1);

  if (!callId || !phone) {
    Logger.write("phoneagent: bad customData, expected 'call_id|phone', got: " + raw);
    VoxEngine.terminate();
    return;
  }

  const call = VoxEngine.callPSTN(phone, CALLER_ID);

  call.addEventListener(CallEvents.Connected, function () {
    const ws = VoxEngine.createWebSocket("wss://" + BACKEND_WSS_HOST + "/ws/voxengine/" + callId, {
      headers: [{ name: "X-Webhook-Secret", value: WEBHOOK_SECRET }],
    });

    ws.addEventListener(WebSocketEvents.OPEN, function () {
      Logger.write("phoneagent: bridge open for call " + callId);
      // Оба направления, дефолт PCM16_8KHZ (совпадает с SAMPLE_RATE PhoneAgent).
      call.sendMediaTo(ws);
      ws.sendMediaTo(call);
    });

    ws.addEventListener(WebSocketEvents.ERROR, function (e) {
      Logger.write("phoneagent: bridge error for call " + callId + ": " + JSON.stringify(e));
    });

    ws.addEventListener(WebSocketEvents.CLOSE, function () {
      Logger.write("phoneagent: bridge closed for call " + callId);
    });

    call.addEventListener(CallEvents.Disconnected, function () {
      ws.stopMediaTo(call);
      ws.close();
    });
  });

  call.addEventListener(CallEvents.Failed, function (e) {
    Logger.write("phoneagent: call failed for " + callId + ": " + JSON.stringify(e));
    VoxEngine.terminate();
  });
});

// Наш бэкенд просит завершить звонок через HTTP-запрос на
// media_session_access_secure_url, полученный из ответа StartScenarios.
// Платформа сама завершает сессию после такого запроса — этот обработчик
// только логирует, ничего не должен возвращать/вызывать сам.
VoxEngine.addEventListener(AppEvents.HttpRequest, function () {
  Logger.write("phoneagent: hangup requested via media_session_access_secure_url");
});
