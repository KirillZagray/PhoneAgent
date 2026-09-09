"""Mock-провайдер телефонии для разработки без реальных звонков.

Используется в dev/test режиме. Вместо реального звонка открывает
текстовую консоль: ввод — что говорит клиент, вывод — что отвечает агент.

Поддерживает аудио в виде байтов (для тестов с пайплайном STT/TTS).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from phoneagent.models.call import CallEvent, CallRef, CallStatus, CallStatusEnum
from phoneagent.providers.telephony.base import BaseTelephonyProvider
from phoneagent.utils import get_logger, mask_phone

logger = get_logger(__name__)


class MockTelephonyProvider(BaseTelephonyProvider):
    """Имитация телефонного звонка для локальной разработки."""

    name = "mock"
    supports_realtime_audio = True

    def __init__(self) -> None:
        self._events: asyncio.Queue[CallEvent] = asyncio.Queue()
        self._calls: dict[str, CallRef] = {}
        self._audio_queues: dict[str, asyncio.Queue[bytes]] = {}
        self._connected = False

    async def connect(self) -> None:
        self._connected = True
        logger.info("mock_telephony_connected")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("mock_telephony_disconnected")

    async def make_call(self, to_phone: str, **kwargs: Any) -> CallRef:
        call_id = f"mock-{to_phone}-{asyncio.get_event_loop().time():.0f}"
        ref = CallRef(
            call_id=call_id,
            provider=self.name,
            phone=to_phone,
            status=CallStatusEnum.INITIATED,
            metadata=kwargs,
        )
        self._calls[call_id] = ref
        self._audio_queues[call_id] = asyncio.Queue()

        # Эмулируем события звонка
        await self._events.put(
            CallEvent(event_type="started", call_id=call_id, payload={"to": to_phone})
        )
        ref.status = CallStatusEnum.RINGING

        await asyncio.sleep(0.1)
        # Имитируем подключение клиента
        ref.status = CallStatusEnum.CONNECTED
        await self._events.put(CallEvent(event_type="connected", call_id=call_id))

        logger.info("mock_call_connected", call_id=call_id, phone=mask_phone(to_phone))
        return ref

    async def hangup(self, call_id: str) -> None:
        if call_id in self._calls:
            self._calls[call_id].status = CallStatusEnum.COMPLETED
            await self._events.put(CallEvent(event_type="ended", call_id=call_id))
            logger.info("mock_call_hangup", call_id=call_id)

    async def get_status(self, call_id: str) -> CallStatus:
        ref = self._calls.get(call_id)
        if ref is None:
            return CallStatus(call_id=call_id, status=CallStatusEnum.FAILED)
        return CallStatus(call_id=call_id, status=ref.status)

    async def events(self) -> AsyncIterator[CallEvent]:
        """Эмулирует поток событий от провайдера (async generator)."""
        while self._connected:
            try:
                event = await asyncio.wait_for(self._events.get(), timeout=1.0)
                yield event
            except TimeoutError:
                continue

    async def send_audio(self, call_id: str, audio: AsyncIterator[bytes]) -> None:
        """Принимает TTS-аудио (в моке просто логирует размер)."""
        if call_id not in self._audio_queues:
            return
        total = 0
        async for chunk in audio:
            total += len(chunk)
            await self._audio_queues[call_id].put(chunk)
        logger.debug("mock_audio_received", call_id=call_id, bytes=total)

    async def send_text(self, call_id: str, text: str) -> None:
        """В моке 'произносит' текст через stdout."""
        print(f"\n🤖 Ассистент: {text}\n")
        logger.info("mock_tts_text", call_id=call_id, text=text)

    # ── Методы для тестов и dev-режима ────────────────

    async def inject_user_audio(self, call_id: str, text: str) -> None:
        """Имитирует фразу клиента (для тестов).

        В реальном моке для интерактивного режима используется отдельный CLI.
        """
        await self._events.put(
            CallEvent(
                event_type="audio",
                call_id=call_id,
                payload={"text": text, "mock": True},
            )
        )
        logger.info("mock_user_input", call_id=call_id, text=text)

    def get_received_audio(self, call_id: str) -> bytes:
        """Возвращает всё аудио, которое 'произнёс' ассистент."""
        q = self._audio_queues.get(call_id)
        if not q:
            return b""
        chunks: list[bytes] = []
        while not q.empty():
            chunks.append(q.get_nowait())
        return b"".join(chunks)
