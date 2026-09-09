"""Orchestrator — главный цикл звонка.

Связывает Telephony + STT + LLM + TTS + Booking.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from phoneagent.config import get_settings
from phoneagent.connectors.factory import build_booking_connector
from phoneagent.connectors.base import BaseBookingConnector
from phoneagent.core.agent import (
    AgentResponse,
    BaseLLMAgent,
    TOOL_DESCRIPTIONS,
    build_llm_agent,
)
from phoneagent.core.state_store import BaseStateStore, build_state_store
from phoneagent.models.booking import BookingRequest
from phoneagent.models.conversation import (
    ConversationState,
    ConversationStep,
    Message,
    Role,
)
from phoneagent.providers.factory import (
    build_stt_provider,
    build_telephony_provider,
    build_tts_provider,
)
from phoneagent.providers.stt.base import BaseSTTProvider
from phoneagent.providers.telephony.base import BaseTelephonyProvider
from phoneagent.providers.tts.base import BaseTTSProvider
from phoneagent.utils import get_logger

logger = get_logger(__name__)


class Orchestrator:
    """Управляет полным циклом телефонного звонка с AI-ассистентом.

    Цикл одного звонка:
        1. Инициализация звонка через telephony_provider.make_call()
        2. Приветствие клиента (TTS → send_audio)
        3. Получение аудио от клиента (events)
        4. Распознавание речи (STT)
        5. Передача текста LLM-агенту
        6. Получение ответа от агента + tool calls
        7. Выполнение tool calls через booking_connector
        8. Синтез ответа (TTS) → клиенту
        9. Завершение/эскалация
    """

    def __init__(
        self,
        telephony: BaseTelephonyProvider,
        stt: BaseSTTProvider,
        tts: BaseTTSProvider,
        llm: BaseLLMAgent,
        booking: BaseBookingConnector,
        state_store: BaseStateStore,
    ) -> None:
        self.telephony = telephony
        self.stt = stt
        self.tts = tts
        self.llm = llm
        self.booking = booking
        self.state_store = state_store
        self.settings = get_settings()

    async def handle_callback(
        self,
        client_phone: str,
        salon_id: str,
        *,
        language: str | None = None,
    ) -> str:
        """Точка входа: клиент нажал "Перезвонить" → инициируем звонок.

        Returns:
            call_id
        """
        language = language or self.settings.default_language

        # 1. Инициируем звонок
        call_ref = await self.telephony.make_call(
            client_phone,
            webhook_url=str(self.settings.public_webhook_base_url),
        )
        logger.info("call_initiated", call_id=call_ref.call_id, phone=client_phone)

        # 2. Создаём начальное состояние
        state = ConversationState(
            call_id=call_ref.call_id,
            salon_id=salon_id,
            client_phone=client_phone,
            language=language,
            step=ConversationStep.GREETING,
        )
        await self.state_store.set(state)

        # 3. Запускаем обработку звонка в фоне
        asyncio.create_task(self._process_call(call_ref.call_id))

        return call_ref.call_id

    async def _process_call(self, call_id: str) -> None:
        """Главный цикл обработки одного звонка."""
        state = await self.state_store.get(call_id)
        if state is None:
            logger.error("call_state_missing", call_id=call_id)
            return

        try:
            # Приветствие
            await self._say(state, f"Здравствуйте! Это салон красоты. Я AI-ассистент, помогу записаться на услугу.")

            # Главный цикл диалога
            while state.step not in (ConversationStep.END, ConversationStep.ESCALATE):
                # Ждём от клиента аудио/текст
                user_text = await self._listen(state)
                if not user_text:
                    # Таймаут / нет ответа
                    if state.retry_count >= self.settings.max_retries:
                        await self._say(state, "К сожалению, я вас не слышу. До свидания!")
                        state.step = ConversationStep.END
                        break
                    state.retry_count += 1
                    await self._say(state, "Алло, вы меня слышите?")
                    continue

                state.retry_count = 0
                state.messages.append(Message(role=Role.USER, content=user_text))
                state.updated_at = datetime.now()

                # Один turn диалога
                response = await self.llm.run_turn(state, user_text, TOOL_DESCRIPTIONS)

                # Выполняем tool calls
                for tool_call in response.tool_calls:
                    tool_result = await self._execute_tool(state, tool_call.name, tool_call.arguments)
                    state.messages.append(
                        Message(
                            role=Role.TOOL,
                            content=str(tool_result),
                            tool_call_id=tool_call.call_id,
                        )
                    )
                    # Если агент закончил — даём ещё один turn с результатом
                    response = await self.llm.run_turn(state, "", TOOL_DESCRIPTIONS)

                # Произносим ответ
                if response.text:
                    await self._say(state, response.text)
                    state.messages.append(Message(role=Role.ASSISTANT, content=response.text))

                # Если финальный turn — выходим
                if response.is_final and state.step == ConversationStep.CONFIRMATION:
                    state.step = ConversationStep.END

                await self.state_store.set(state)

            # Завершение
            await self.telephony.hangup(call_id)
            logger.info("call_completed", call_id=call_id, step=state.step.value)

        except Exception as e:
            logger.error("call_failed", call_id=call_id, error=str(e), exc_info=True)
            try:
                await self.telephony.hangup(call_id)
            except Exception:
                pass

    async def _listen(self, state: ConversationState) -> str:
        """Получает реплику клиента.

        В mock-режиме — читает из инжектированного STT-текста.
        В реальном — стримит аудио из telephony → STT.
        """
        # В mock-режиме: ждём инжект через STT provider
        if isinstance(self.stt, type(self.stt).__mro__[0]) and self.stt.name == "mock":  # type: ignore[attr-defined]
            # Подождём чуть-чуть и вернём инжектированный текст
            await asyncio.sleep(0.5)
            # У мок-провайдеров есть инжект — здесь упрощённо
            # В реальной реализации — подписка на события telephony

        # Заглушка: в реальной реализации — чтение из очереди событий
        # и стриминг аудио в STT
        return ""

    async def _say(self, state: ConversationState, text: str) -> None:
        """Синтезирует речь и отправляет клиенту."""
        logger.info("agent_says", call_id=state.call_id, text=text)

        # 1. TTS
        async def audio_stream() -> Any:
            async for chunk in self.tts.synthesize_stream(
                text, language=state.language, sample_rate=self.settings.sample_rate
            ):
                yield chunk

        # 2. Отправляем в звонок
        await self.telephony.send_audio(state.call_id, audio_stream())
        # Также отправляем текстом (для провайдеров с встроенным TTS)
        try:
            await self.telephony.send_text(state.call_id, text)
        except NotImplementedError:
            pass

    async def _execute_tool(self, state: ConversationState, name: str, arguments: dict[str, Any]) -> Any:
        """Выполняет tool call через booking connector."""
        try:
            if name == "list_services":
                services = await self.booking.list_services()
                return [{"id": s.id, "name": s.name, "duration": s.duration_minutes, "price": s.price}
                        for s in services]

            if name == "list_masters":
                masters = await self.booking.list_masters(arguments.get("service_id"))
                return [{"id": m.id, "name": m.name, "rating": m.rating} for m in masters]

            if name == "list_slots":
                target_date = date.fromisoformat(arguments["date"])
                slots = await self.booking.list_slots(
                    master_id=arguments["master_id"],
                    target_date=target_date,
                    service_id=arguments.get("service_id"),
                )
                return [
                    {"id": s.id, "start_at": s.start_at.isoformat(), "duration": s.duration_minutes}
                    for s in slots
                ]

            if name == "create_booking":
                req = BookingRequest(
                    service_id=arguments["service_id"],
                    master_id=arguments["master_id"],
                    date=date.fromisoformat(arguments["date"]),
                    time=arguments["time"],
                    client_phone=state.client_phone,
                    client_name=arguments.get("client_name"),
                )
                booking = await self.booking.create_booking(req)
                # Сохраняем результат в state
                state.slot = booking.slot
                state.service = booking.service
                state.master = booking.master
                state.step = ConversationStep.CONFIRMATION
                return {"success": True, "booking_id": booking.id, "when": booking.slot.start_at.isoformat()}

            msg = f"Unknown tool: {name}"
            return {"error": msg}

        except Exception as e:
            logger.error("tool_execution_failed", tool=name, error=str(e))
            return {"error": str(e)}


async def build_orchestrator() -> Orchestrator:
    """Собирает все провайдеры и создаёт orchestrator."""
    settings = get_settings()

    telephony = build_telephony_provider()
    stt = build_stt_provider()
    tts = build_tts_provider()
    llm = build_llm_agent()
    booking = build_booking_connector()
    state_store = build_state_store()

    # Подключаем асинхронные клиенты
    if hasattr(state_store, "connect"):
        await state_store.connect()
    await llm.connect()
    await stt.connect()
    await tts.connect()
    await booking.connect()
    await telephony.connect()

    return Orchestrator(
        telephony=telephony,
        stt=stt,
        tts=tts,
        llm=llm,
        booking=booking,
        state_store=state_store,
    )


__all__ = ["Orchestrator", "build_orchestrator"]