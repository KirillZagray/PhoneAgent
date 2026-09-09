"""Twilio — международный провайдер (для тестов в dev за рубежом).

Документация: https://www.twilio.com/docs/voice

Заготовка: реализация для тестового стенда.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from phoneagent.config import get_settings
from phoneagent.models.call import CallEvent, CallRef, CallStatus, CallStatusEnum
from phoneagent.providers.telephony.base import BaseTelephonyProvider
from phoneagent.utils import get_logger

logger = get_logger(__name__)

TWILIO_API_URL = "https://api.twilio.com/2010-04-01"


class TwilioTelephonyProvider(BaseTelephonyProvider):
    """Провайдер Twilio."""

    name = "twilio"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        if not self.settings.twilio_account_sid or not self.settings.twilio_auth_token:
            msg = "Twilio credentials not configured"
            raise RuntimeError(msg)
        auth = (self.settings.twilio_account_sid, self.settings.twilio_auth_token)
        self._client = httpx.AsyncClient(base_url=TWILIO_API_URL, auth=auth, timeout=30.0)
        logger.info("twilio_connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def make_call(self, to_phone: str, **kwargs: Any) -> CallRef:
        webhook_url = kwargs.get("webhook_url") or ""
        body = {
            "To": to_phone,
            "From": self.settings.twilio_caller_id,
            "Url": webhook_url,
        }
        if self._client is None:
            msg = "Twilio client not connected"
            raise RuntimeError(msg)

        response = await self._client.post(f"/Accounts/{self.settings.twilio_account_sid}/Calls.json", data=body)
        response.raise_for_status()
        data = response.json()
        call_id = str(data.get("sid", ""))
        return CallRef(
            call_id=call_id,
            provider=self.name,
            phone=to_phone,
            status=CallStatusEnum.INITIATED,
        )

    async def hangup(self, call_id: str) -> None:
        if self._client is None:
            return
        await self._client.post(
            f"/Accounts/{self.settings.twilio_account_sid}/Calls/{call_id}.json",
            data={"Status": "completed"},
        )

    async def get_status(self, call_id: str) -> CallStatus:
        if self._client is None:
            return CallStatus(call_id=call_id, status=CallStatusEnum.PENDING)
        response = await self._client.get(
            f"/Accounts/{self.settings.twilio_account_sid}/Calls/{call_id}.json"
        )
        response.raise_for_status()
        data = response.json()
        status_map: dict[str, CallStatusEnum] = {
            "completed": CallStatusEnum.COMPLETED,
            "in-progress": CallStatusEnum.CONNECTED,
            "ringing": CallStatusEnum.RINGING,
            "no-answer": CallStatusEnum.NO_ANSWER,
            "busy": CallStatusEnum.BUSY,
            "failed": CallStatusEnum.FAILED,
        }
        return CallStatus(
            call_id=call_id,
            status=status_map.get(data.get("status", ""), CallStatusEnum.PENDING),
            duration_seconds=int(data.get("duration", "0") or 0),
        )

    async def events(self) -> AsyncIterator[CallEvent]:
        # События приходят через webhooks
        while False:
            yield  # type: ignore[unreachable]

    async def send_audio(self, call_id: str, audio: AsyncIterator[bytes]) -> None:
        # Twilio использует <Stream> TwiML для bidirectional audio
        msg = "send_audio: Twilio TwiML <Stream> integration"
        raise NotImplementedError(msg)

    async def send_text(self, call_id: str, text: str) -> None:
        # Twilio <Say> verb
        msg = "send_text: Twilio <Say> verb"
        raise NotImplementedError(msg)