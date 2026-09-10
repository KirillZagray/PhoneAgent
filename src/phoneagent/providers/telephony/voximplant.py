"""Voximplant — российский провайдер телефонии.

Реализует BaseTelephonyProvider через Voximplant Management API + VoxEngine Scenario.

Всё ниже сверено с https://voximplant.com/docs/ вживую (2026-09-09), а не по
памяти — предыдущая версия этого файла вызывала несуществующий метод
`StartCall`. Реальный механизм:

1. Мы вызываем `StartScenarios` (не `StartCall` — такого метода нет). У него
   нет параметра `phone`: он просто запускает JS-сценарий в новой медиа-сессии,
   привязанной к `rule_id`. Номер для дозвона сценарий берёт из
   `script_custom_data` и сам вызывает `VoxEngine.callPSTN(number, callerId)`.
2. `script_custom_data` ограничен 200 байтами и читается в сценарии как
   `VoxEngine.customData()` — просто строка `"{call_id}|{phone}"`, без JSON.
   `call_id` минтим сами (uuid), ДО вызова: он нужен сценарию, чтобы открыть
   `wss://.../ws/voxengine/{call_id}` (аудио-мост, см.
   docs/superpowers/specs/2026-09-09-voximplant-audio-bridge-design.md).
3. Ответ `StartScenarios` возвращает `call_session_history_id` (для
   `GetCallHistory`) и `media_session_access_secure_url` — единственный способ
   остановить сессию извне: HTTP-запрос на этот URL триггерит в сценарии
   событие `AppEvents.HttpRequest`, на которое сценарий должен сам повесить
   `call.hangup()`. Отдельного `StopCall` метода не существует.

Оба этих значения — внутренние для Voximplant, наружу (в CallRef, Orchestrator,
state_store) утекает только наш `call_id`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from phoneagent.config import get_settings
from phoneagent.core.audio_session import get_session, wait_for_session
from phoneagent.models.call import CallEvent, CallRef, CallStatus, CallStatusEnum
from phoneagent.providers.telephony.base import BaseTelephonyProvider
from phoneagent.utils import get_logger, mask_phone

logger = get_logger(__name__)

VOXIMPLANT_API_URL = "https://api.voximplant.com/platform_api"


@dataclass
class _Session:
    call_session_history_id: str
    media_session_access_secure_url: str


class VoximplantTelephonyProvider(BaseTelephonyProvider):
    """Провайдер телефонии Voximplant."""

    name = "voximplant"
    # Phase B: send_audio реально доставляет звук (через AudioSession + мост
    # api/media_ws.py) — Orchestrator.handle_callback больше не отказывает
    # в реальном звонке для этого провайдера.
    supports_realtime_audio = True

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None
        self._sessions: dict[str, _Session] = {}  # наш call_id -> данные сессии Voximplant

    async def connect(self) -> None:
        if not self.settings.voximplant_account_id or not self.settings.voximplant_api_key:
            msg = "Voximplant credentials not configured (VOXIMPLANT_ACCOUNT_ID, VOXIMPLANT_API_KEY)"
            raise RuntimeError(msg)
        self._client = httpx.AsyncClient(timeout=30.0)
        logger.info("voximplant_connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
        logger.info("voximplant_disconnected")

    async def _call_api(self, method: str, **params: Any) -> dict[str, Any]:
        """Вызов метода Voximplant Management API: POST /platform_api/{Method}."""
        if self._client is None:
            msg = "Voximplant client not connected"
            raise RuntimeError(msg)

        params = {
            "account_id": self.settings.voximplant_account_id,
            "api_key": self.settings.voximplant_api_key,
            **params,
        }
        response = await self._client.post(f"{VOXIMPLANT_API_URL}/{method}", data=params)
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            msg = f"Voximplant API error: {result['error']}"
            raise RuntimeError(msg)
        return result  # type: ignore[no-any-return]

    async def make_call(self, to_phone: str, **kwargs: Any) -> CallRef:
        """Запускает VoxEngine-сценарий через StartScenarios.

        Сам дозвон делает сценарий (`VoxEngine.callPSTN`), не эта функция —
        см. докстринг модуля.
        """
        rule_id = kwargs.get("rule_id") or self.settings.voximplant_rule_id

        call_id = uuid.uuid4().hex
        result = await self._call_api(
            "StartScenarios",
            rule_id=rule_id,
            script_custom_data=f"{call_id}|{to_phone}",
        )
        session_history_id = str(result.get("call_session_history_id") or "")
        access_url = str(result.get("media_session_access_secure_url") or "")
        if not session_history_id or not access_url:
            msg = f"Voximplant StartScenarios returned incomplete response: {list(result)}"
            raise RuntimeError(msg)
        self._sessions[call_id] = _Session(session_history_id, access_url)

        ref = CallRef(
            call_id=call_id,
            provider=self.name,
            phone=to_phone,
            status=CallStatusEnum.INITIATED,
            metadata={"rule_id": rule_id, "voximplant_session_id": session_history_id},
        )
        logger.info("voximplant_call_initiated", call_id=call_id, phone=mask_phone(to_phone))
        return ref

    async def hangup(self, call_id: str) -> None:
        """Останавливает звонок.

        Для исходящих (StartScenarios) — через `media_session_access_secure_url`.
        Реального REST-метода `StopCall` не существует: единственный способ
        достучаться до запущенного сценария извне — HTTP-запрос на этот URL,
        который в сценарии приходит как `AppEvents.HttpRequest` (сценарий
        должен сам на него повесить `call.hangup()`, см. voxengine/*.js).

        Для входящих такого URL нет вообще (сценарий не запускался через
        StartScenarios) — единственный канал наружу к сценарию это сам
        WS-мост, поэтому просим AudioSession подать сигнал (см. её
        hangup_requested) — мост сам закроет соединение, а обработчик
        WebSocketEvents.CLOSE в inbound-сценарии вызовет call.hangup().
        """
        session = self._sessions.pop(call_id, None)
        if session is not None:
            if self._client is None:
                msg = "Voximplant client not connected"
                raise RuntimeError(msg)
            await self._client.get(session.media_session_access_secure_url)
            logger.info("voximplant_call_hangup", call_id=call_id)
            return

        audio_session = get_session(call_id)
        if audio_session is None:
            logger.warning("voximplant_hangup_unknown_call", call_id=call_id)
            return
        audio_session.request_hangup()
        logger.info("voximplant_call_hangup_requested_inbound", call_id=call_id)

    async def get_status(self, call_id: str) -> CallStatus:
        """Получает статус через GetCallHistory (пока упрощённо)."""
        session = self._sessions.get(call_id)
        session_id = session.call_session_history_id if session else call_id
        result = await self._call_api("GetCallHistory", call_session_history_id=session_id)
        history = result.get("result", [])
        if not history:
            return CallStatus(call_id=call_id, status=CallStatusEnum.PENDING)

        record = history[0]
        return CallStatus(
            call_id=call_id,
            status=CallStatusEnum.COMPLETED if record.get("duration") else CallStatusEnum.CONNECTED,
            duration_seconds=int(record.get("duration", 0)),
        )

    async def events(self) -> AsyncIterator[CallEvent]:
        """Поток событий — для реального продакшена используется WebHook API.

        В этой заготовке предполагается, что VoxEngine Scenario отправляет
        события на /webhooks/voximplant через POST запросы.
        """
        # Реальная реализация потребует polling или WebSocket.
        # Для MVP события приходят через webhook endpoint (см. api/webhooks.py)
        while False:
            yield

    async def send_audio(self, call_id: str, audio: AsyncIterator[bytes]) -> None:
        """Кладёт чанки TTS в AudioSession.outgoing — реально их отправляет
        мост `api/media_ws.py` (там же реальный тайминг, см. paced_frames).

        Ждёт до `wait_for_session`-таймаута, если мост ещё не успел
        подключиться (гонка: make_call() уже вернулся, а VoxEngine откроет
        WS только после CallEvents.Connected — на секунду-другую позже).

        Возвращается только когда мост реально забрал все чанки из очереди
        (см. wait_drained) — не когда они просто положены туда. Orchestrator
        полагается на это: _say() не должен считаться завершённым, пока
        агент физически не договорил, иначе _listen() начнёт слушать поверх
        ещё звучащей на линии речи агента.
        """
        session = await wait_for_session(call_id)
        if session is None:
            logger.warning("voximplant_send_audio_no_session", call_id=call_id)
            return
        async for chunk in audio:
            session.push_outgoing(chunk)
        await session.wait_drained()

    async def send_text(self, call_id: str, text: str) -> None:
        """Не используется — TTS всегда наш пайплайн, не встроенный SpeechKit."""
        msg = "send_text: не применяется, TTS идёт через send_audio/аудио-мост"
        raise NotImplementedError(msg)
