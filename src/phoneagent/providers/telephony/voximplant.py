"""Voximplant — российский провайдер телефонии.

Реализует BaseTelephonyProvider через Voximplant HTTP API + VoxEngine Scenario.

Документация: https://voximplant.com/docs/

Для стриминга аудио используется Voximplant Audio Streaming API:
https://voximplant.com/docs/references/voxengine/audiostreaming

Заготовка: реализация методов будет добавлена по мере необходимости.
Полноценный код появится после этапа 2 (MVP с реальными звонками).
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from phoneagent.config import get_settings
from phoneagent.models.call import CallEvent, CallRef, CallStatus, CallStatusEnum
from phoneagent.providers.telephony.base import BaseTelephonyProvider
from phoneagent.utils import get_logger

logger = get_logger(__name__)

VOXIMPLANT_API_URL = "https://api.voximplant.com/platform_api"


class VoximplantTelephonyProvider(BaseTelephonyProvider):
    """Провайдер телефонии Voximplant."""

    name = "voximplant"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        if not self.settings.voximplant_account_id or not self.settings.voximplant_api_key:
            msg = "Voximplant credentials not configured (VOXIMPLANT_ACCOUNT_ID, VOXIMPLANT_API_KEY)"
            raise RuntimeError(msg)
        self._client = httpx.AsyncClient(base_url=VOXIMPLANT_API_URL, timeout=30.0)
        logger.info("voximplant_connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
        logger.info("voximplant_disconnected")

    async def _call_api(self, method: str, **params: Any) -> dict[str, Any]:
        """Вызов метода Voximplant API.

        Используется JSON API: POST /platform_api/{method}
        Auth: account_id + api_key в query params.
        """
        if self._client is None:
            msg = "Voximplant client not connected"
            raise RuntimeError(msg)

        # Voximplant принимает multipart/form-data для JSON API
        params = {
            "account_id": self.settings.voximplant_account_id,
            "api_key": self.settings.voximplant_api_key,
            **params,
        }
        response = await self._client.post(
            f"/{method}",
            data=params,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            msg = f"Voximplant API error: {result['error']}"
            raise RuntimeError(msg)
        return result  # type: ignore[no-any-return]

    async def make_call(self, to_phone: str, **kwargs: Any) -> CallRef:
        """Инициирует звонок через Voximplant StartCall.

        https://voximplant.com/docs/references/httpapi/StartCall
        """
        rule_id = kwargs.get("rule_id") or self.settings.voximplant_rule_id
        scenario_id = kwargs.get("scenario_id") or self.settings.voximplant_scenario_id
        webhook_url = kwargs.get("webhook_url")

        result = await self._call_api(
            "StartCall",
            rule_id=rule_id,
            phone=to_phone,
            script_custom_data=base64.b64encode(
                json.dumps({"webhook_url": webhook_url, "scenario_id": scenario_id}).encode()
            ).decode(),
        )
        call_id = str(result.get("call_id", ""))
        if not call_id:
            msg = f"Voximplant StartCall returned no call_id: {result}"
            raise RuntimeError(msg)

        ref = CallRef(
            call_id=call_id,
            provider=self.name,
            phone=to_phone,
            status=CallStatusEnum.INITIATED,
            metadata={"rule_id": rule_id, "scenario_id": scenario_id},
        )
        logger.info("voximplant_call_initiated", call_id=call_id, phone=to_phone)
        return ref

    async def hangup(self, call_id: str) -> None:
        """Завершает звонок через StopCall."""
        await self._call_api("StopCall", call_id=call_id, reason="hangup")
        logger.info("voximplant_call_hangup", call_id=call_id)

    async def get_status(self, call_id: str) -> CallStatus:
        """Получает статус через GetCallHistory (пока упрощённо)."""
        result = await self._call_api("GetCallHistory", call_id=call_id)
        history = result.get("result", [])
        if not history:
            return CallStatus(call_id=call_id, status=CallStatusEnum.PENDING)

        record = history[0]
        # Voximplant возвращает длительность, флаги и т.п.
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
        """Стриминг аудио в звонок через MediaStream (TTS через VoxEngine).

        Заготовка: реализация потребует интеграции с VoxEngine Scenario
        и AudioStreaming API.
        """
        msg = "send_audio: реализуется через VoxEngine Scenario + MediaStream"
        raise NotImplementedError(msg)

    async def send_text(self, call_id: str, text: str) -> None:
        """Если VoxEngine Scenario использует встроенный TTS (SpeechKit и т.п.)."""
        msg = "send_text: реализуется через VoxEngine Scenario + встроенный TTS"
        raise NotImplementedError(msg)