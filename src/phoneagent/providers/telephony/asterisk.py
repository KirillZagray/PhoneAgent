"""Asterisk (self-hosted) — ARI + AudioSocket.

Интеграция через Asterisk REST Interface (ARI) для управления звонком
(originate/hangup/статус/события) и AudioSocket (двоичный TCP-протокол
Asterisk, app_audiosocket) для самого аудио — по той же схеме, что и
Voximplant Phase A: транспорт аудио отдельно от control-плейна, см.
`phoneagent/api/audiosocket_server.py`.

Поток исходящего звонка:
    1. make_call: POST /ari/channels — Originate канала через PJSIP-эндпоинт
       (SIP-транк, например MTS Exolve), сразу в Stasis-приложение
       (`asterisk_stasis_app`), с channel-переменной PHONEAGENT_CALL_ID.
    2. Приходит событие StasisStart (ловим в фоновой задаче, читающей ARI
       events WebSocket) — переводим канал в dialplan (`channels/{id}/continue`)
       на контекст, который делает Answer() + AudioSocket(call_id, host:port).
       Именно AudioSocket() открывает TCP-соединение на наш аудио-сервер —
       это отдельный канал, ARI тут дальше не участвует в передаче звука.
    3. hangup: DELETE /ari/channels/{id} — работает независимо от того,
       остался ли канал под Stasis или уже ушёл в dialplan (ID не меняется).

Документация: https://docs.asterisk.org/Configuration/Interfaces/Asterisk-REST-Interface-ARI/
AudioSocket: https://docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

from phoneagent.config import get_settings
from phoneagent.core.audio_session import wait_for_session
from phoneagent.models.call import CallEvent, CallRef, CallStatus, CallStatusEnum
from phoneagent.providers.telephony.base import BaseTelephonyProvider
from phoneagent.utils import get_logger, mask_phone

logger = get_logger(__name__)

# Asterisk ARI channel state -> наш CallStatusEnum. Каналы, которых нет в этой
# карте (например "Reserved" на очень раннем этапе), маппятся на PENDING.
_ARI_STATE_MAP: dict[str, CallStatusEnum] = {
    "Down": CallStatusEnum.PENDING,
    "Rsrvd": CallStatusEnum.PENDING,
    "OffHook": CallStatusEnum.INITIATED,
    "Dialing": CallStatusEnum.RINGING,
    "Ring": CallStatusEnum.RINGING,
    "Ringing": CallStatusEnum.RINGING,
    "Up": CallStatusEnum.CONNECTED,
    "Busy": CallStatusEnum.BUSY,
}


class AsteriskTelephonyProvider(BaseTelephonyProvider):
    """Провайдер Asterisk через ARI + AudioSocket."""

    name = "asterisk"
    # Phase B: send_audio реально доставляет звук (через AudioSession + мост
    # api/audiosocket_server.py).
    supports_realtime_audio = True

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None
        self._ws: ClientConnection | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._events: asyncio.Queue[CallEvent] = asyncio.Queue()
        # наш call_id -> ARI channel id. Нужен для hangup/get_status, потому что
        # наружу (Orchestrator, state_store) мы отдаём только свой call_id, как
        # и Voximplant-провайдер — см. его докстринг про _sessions.
        self._channels: dict[str, str] = {}

    @property
    def _ari_base_url(self) -> str:
        return f"http://{self.settings.asterisk_host}:{self.settings.asterisk_ari_port}/ari"

    @property
    def _ari_ws_url(self) -> str:
        return (
            f"ws://{self.settings.asterisk_host}:{self.settings.asterisk_ari_port}/ari/events"
            f"?api_key={self.settings.asterisk_ari_username}:{self.settings.asterisk_ari_password}"
            f"&app={self.settings.asterisk_stasis_app}"
        )

    async def connect(self) -> None:
        if not self.settings.asterisk_host or not self.settings.asterisk_ari_username:
            msg = "Asterisk credentials not configured (ASTERISK_HOST, ASTERISK_ARI_USERNAME, ASTERISK_ARI_PASSWORD)"
            raise RuntimeError(msg)

        self._client = httpx.AsyncClient(
            base_url=self._ari_base_url,
            auth=(self.settings.asterisk_ari_username, self.settings.asterisk_ari_password),
            timeout=30.0,
        )
        self._ws = await websockets.connect(self._ari_ws_url)
        self._pump_task = asyncio.create_task(self._pump_events())
        logger.info("asterisk_connected", ari_url=self._ari_base_url)

    async def disconnect(self) -> None:
        if self._pump_task:
            self._pump_task.cancel()
        if self._ws:
            await self._ws.close()
        if self._client:
            await self._client.aclose()
        logger.info("asterisk_disconnected")

    async def _pump_events(self) -> None:
        """Фоновая задача: читает ARI events WS, продвигает StasisStart в
        dialplan (Answer + AudioSocket) и кладёт CallEvent в очередь для
        `events()`. Живёт всё время жизни провайдера — падение здесь молча
        обрывает поток событий, поэтому обёрнуто в try/except с логом, а не
        просто `async for`.
        """
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("asterisk_bad_ari_json")
                    continue
                await self._handle_ari_event(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("asterisk_event_pump_crashed")

    async def _handle_ari_event(self, message: dict[str, Any]) -> None:
        event_type = message.get("type")
        channel = message.get("channel") or {}
        ari_channel_id = channel.get("id")
        call_id = (channel.get("channelvars") or {}).get("PHONEAGENT_CALL_ID") or self._call_id_for(
            ari_channel_id
        )

        if event_type == "StasisStart":
            logger.info("asterisk_stasis_start", call_id=call_id, ari_channel_id=ari_channel_id)
            if call_id and ari_channel_id:
                self._channels[call_id] = ari_channel_id
            if ari_channel_id:
                await self._continue_to_bridge(ari_channel_id)
            if call_id:
                await self._events.put(CallEvent(event_type="started", call_id=call_id))
        elif event_type in ("StasisEnd", "ChannelDestroyed"):
            logger.info("asterisk_channel_ended", call_id=call_id, event_type=event_type)
            if call_id:
                await self._events.put(CallEvent(event_type="ended", call_id=call_id))
        elif event_type == "ChannelStateChange" and channel.get("state") == "Up":
            if call_id:
                await self._events.put(CallEvent(event_type="connected", call_id=call_id))

    def _call_id_for(self, ari_channel_id: str | None) -> str | None:
        if not ari_channel_id:
            return None
        for call_id, cid in self._channels.items():
            if cid == ari_channel_id:
                return call_id
        return None

    async def _continue_to_bridge(self, ari_channel_id: str) -> None:
        """Переводит канал из Stasis в dialplan-контекст, который открывает
        AudioSocket. Отдельная функция — вызывается только из _pump_events,
        но вынесена для читаемости и чтобы ошибка ARI-запроса не роняла сам pump.
        """
        if self._client is None:
            return
        try:
            response = await self._client.post(
                f"/channels/{ari_channel_id}/continue",
                params={"context": "phoneagent-bridge", "extension": "s", "priority": 1},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("asterisk_continue_failed", ari_channel_id=ari_channel_id)

    async def make_call(self, to_phone: str, **kwargs: Any) -> CallRef:
        if self._client is None:
            msg = "Asterisk client not connected"
            raise RuntimeError(msg)

        trunk = kwargs.get("trunk_endpoint") or self.settings.asterisk_trunk_endpoint
        call_id = str(uuid.uuid4())  # с дефисами — так его ждёт AudioSocket() как ID-аргумент

        response = await self._client.post(
            "/channels",
            json={
                "endpoint": f"PJSIP/{to_phone}@{trunk}",
                "app": self.settings.asterisk_stasis_app,
                "callerId": self.settings.asterisk_caller_id or None,
                "variables": {"PHONEAGENT_CALL_ID": call_id},
            },
        )
        response.raise_for_status()
        channel = response.json()
        ari_channel_id = str(channel["id"])
        self._channels[call_id] = ari_channel_id

        ref = CallRef(
            call_id=call_id,
            provider=self.name,
            phone=to_phone,
            status=CallStatusEnum.INITIATED,
            metadata={"trunk": trunk, "ari_channel_id": ari_channel_id},
        )
        logger.info("asterisk_call_initiated", call_id=call_id, phone=mask_phone(to_phone))
        return ref

    async def hangup(self, call_id: str) -> None:
        ari_channel_id = self._channels.pop(call_id, None)
        if ari_channel_id is None:
            logger.warning("asterisk_hangup_unknown_call", call_id=call_id)
            return
        if self._client is None:
            msg = "Asterisk client not connected"
            raise RuntimeError(msg)
        response = await self._client.delete(f"/channels/{ari_channel_id}")
        # 404 значит канал уже сам завершился (например, собеседник положил трубку) — не ошибка.
        if response.status_code not in (204, 404):
            response.raise_for_status()
        logger.info("asterisk_call_hangup", call_id=call_id)

    async def get_status(self, call_id: str) -> CallStatus:
        ari_channel_id = self._channels.get(call_id)
        if ari_channel_id is None or self._client is None:
            return CallStatus(call_id=call_id, status=CallStatusEnum.PENDING)

        response = await self._client.get(f"/channels/{ari_channel_id}")
        if response.status_code == 404:
            return CallStatus(call_id=call_id, status=CallStatusEnum.COMPLETED)
        response.raise_for_status()
        channel = response.json()
        state = str(channel.get("state", ""))
        status = _ARI_STATE_MAP.get(state, CallStatusEnum.PENDING)
        return CallStatus(call_id=call_id, status=status)

    async def events(self) -> AsyncIterator[CallEvent]:
        while True:
            yield await self._events.get()

    async def send_audio(self, call_id: str, audio: AsyncIterator[bytes]) -> None:
        """Кладёт чанки TTS в AudioSession.outgoing — реально их отправляет
        мост `api/audiosocket_server.py` (там же реальный тайминг). Тот же
        паттерн, что и у Voximplant (см. его send_audio, включая
        wait_drained — Orchestrator полагается на то, что send_audio не
        возвращается, пока аудио реально не забрано мостом на отправку).
        """
        session = await wait_for_session(call_id)
        if session is None:
            logger.warning("asterisk_send_audio_no_session", call_id=call_id)
            return
        async for chunk in audio:
            session.push_outgoing(chunk)
        await session.wait_drained()

    async def send_text(self, call_id: str, text: str) -> None:
        msg = "send_text: не применяется, TTS идёт через send_audio/AudioSocket-мост"
        raise NotImplementedError(msg)
