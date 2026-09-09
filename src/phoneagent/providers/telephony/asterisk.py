"""Asterisk (self-hosted) — для тех, кто хочет полный контроль.

Интеграция через Asterisk REST Interface (ARI).
Документация: https://docs.asterisk.org/Configuration/Interfaces/Asterisk-REST-Interface-ARI/

Заготовка: реализация для self-hosted сценариев.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from phoneagent.models.call import CallEvent, CallRef, CallStatus, CallStatusEnum
from phoneagent.providers.telephony.base import BaseTelephonyProvider
from phoneagent.utils import get_logger

logger = get_logger(__name__)


class AsteriskTelephonyProvider(BaseTelephonyProvider):
    """Провайдер Asterisk через ARI."""

    name = "asterisk"

    async def connect(self) -> None:
        msg = "Asterisk ARI client — установите `httpx-websocket` и подключите ARI"
        raise NotImplementedError(msg)

    async def disconnect(self) -> None:
        pass

    async def make_call(self, to_phone: str, **kwargs: Any) -> CallRef:
        # POST /ari/channels {endpoint: "PJSIP/{to_phone}", app: "phoneagent"}
        msg = "Asterisk: POST /ari/channels + originate"
        raise NotImplementedError(msg)

    async def hangup(self, call_id: str) -> None:
        # DELETE /ari/channels/{call_id}
        msg = "Asterisk: DELETE /ari/channels/{id}"
        raise NotImplementedError(msg)

    async def get_status(self, call_id: str) -> CallStatus:
        # GET /ari/channels/{call_id}
        msg = "Asterisk: GET /ari/channels/{id}"
        raise NotImplementedError(msg)

    async def events(self) -> AsyncIterator[CallEvent]:
        # WebSocket /ari/events
        msg = "Asterisk: WebSocket /ari/events"
        raise NotImplementedError(msg)

    async def send_audio(self, call_id: str, audio: AsyncIterator[bytes]) -> None:
        # ExternalMedia + AudioSocket
        msg = "Asterisk: AudioSocket / ExternalMedia"
        raise NotImplementedError(msg)

    async def send_text(self, call_id: str, text: str) -> None:
        msg = "Asterisk: AGI Echo с TTS"
        raise NotImplementedError(msg)