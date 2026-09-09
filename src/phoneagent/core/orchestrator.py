"""Orchestrator — главный цикл звонка.

Связывает Telephony + STT + LLM + TTS + Booking.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime
from typing import Any

import structlog

from phoneagent.config import get_settings
from phoneagent.connectors.base import BaseBookingConnector
from phoneagent.connectors.factory import build_booking_connector
from phoneagent.core.agent import TOOL_DESCRIPTIONS, BaseLLMAgent, ToolExecutor, build_llm_agent
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
from phoneagent.utils import get_logger, mask_phone

logger = get_logger(__name__)

TERMINAL_STEPS = (ConversationStep.END, ConversationStep.ESCALATE)


class DuplicateCallbackError(Exception):
    """На этот номер звонок уже инициирован недавно (двойной клик / спам)."""


class Orchestrator:
    """Управляет полным циклом телефонного звонка с AI-ассистентом.

    Цикл одного звонка:
        1. Инициализация звонка через telephony_provider.make_call()
        2. Приветствие клиента (TTS → send_audio)
        3. Получение аудио от клиента (events)
        4. Распознавание речи (STT)
        5. Передача текста LLM-агенту (агент сам разруливает tool calls)
        6. Синтез ответа (TTS) → клиенту
        7. Завершение/эскалация
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
        # Держим ссылки на фоновые задачи звонков — иначе asyncio может
        # собрать таск сборщиком мусора до его завершения.
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def aclose(self) -> None:
        """Закрывает все клиенты провайдеров (вызывается при shutdown приложения)."""
        for coro in (
            self.telephony.disconnect(),
            self.stt.disconnect(),
            self.tts.disconnect(),
            self.llm.disconnect(),
            self.booking.disconnect(),
        ):
            try:
                await coro
            except Exception:
                logger.warning("provider_disconnect_failed", exc_info=True)
        disconnect = getattr(self.state_store, "disconnect", None)
        if disconnect is not None:
            try:
                await disconnect()
            except Exception:
                logger.warning("state_store_disconnect_failed", exc_info=True)

    async def _save(self, state: ConversationState) -> None:
        state.updated_at = datetime.now()
        await self.state_store.set(state, ttl_seconds=self.settings.state_ttl_seconds)

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
        if not self.telephony.supports_realtime_audio:
            msg = (
                f"Telephony provider '{self.telephony.name}' does not implement "
                "real-time audio streaming yet (see README roadmap) — refusing to "
                "place a real call that would connect and immediately fail. "
                "Use TELEPHONY_PROVIDER=mock, or /call/text for a text-only demo."
            )
            raise RuntimeError(msg)

        if not await self.state_store.acquire_lock(
            f"callback:{client_phone}", self.settings.callback_dedupe_seconds
        ):
            logger.info("callback_deduplicated", phone=mask_phone(client_phone))
            raise DuplicateCallbackError(client_phone)

        language = language or self.settings.default_language

        # 1. Инициируем звонок
        call_ref = await self.telephony.make_call(
            client_phone,
            webhook_url=str(self.settings.public_webhook_base_url),
        )
        logger.info("call_initiated", call_id=call_ref.call_id, phone=mask_phone(client_phone))

        # 2. Создаём начальное состояние
        state = ConversationState(
            call_id=call_ref.call_id,
            salon_id=salon_id,
            client_phone=client_phone,
            language=language,
            step=ConversationStep.GREETING,
        )
        await self._save(state)

        # 3. Запускаем обработку звонка в фоне (ссылку держим в self._background_tasks)
        task = asyncio.create_task(self._process_call(call_ref.call_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return call_ref.call_id

    async def handle_text_message(
        self,
        *,
        call_id: str | None,
        salon_id: str,
        client_phone: str,
        language: str,
        text: str,
    ) -> dict[str, str]:
        """Текстовый turn без телефонии — для отладки и чат-виджета.

        Если call_id не передан — создаёт новую сессию и возвращает её id,
        дальнейшие сообщения продолжают тот же диалог по этому call_id.
        """
        if call_id:
            state = await self.state_store.get(call_id)
            if state is None:
                msg = f"Unknown call_id: {call_id}"
                raise ValueError(msg)
        else:
            call_id = f"text-{uuid.uuid4().hex[:12]}"
            state = ConversationState(
                call_id=call_id,
                salon_id=salon_id,
                client_phone=client_phone,
                language=language,
                step=ConversationStep.GREETING,
            )

        with structlog.contextvars.bound_contextvars(call_id=call_id):
            if state.step in TERMINAL_STEPS:
                return {"call_id": call_id, "reply": "", "step": state.step.value}

            response = await self._run_turn(state, text)
            await self._save(state)

        return {"call_id": call_id, "reply": response.text, "step": state.step.value}

    async def _run_turn(self, state: ConversationState, user_text: str) -> Any:
        """Один turn: реплика клиента → LLM (+tools) → текст ответа. Двигает FSM."""
        state.messages.append(Message(role=Role.USER, content=user_text))
        response = await self.llm.run_turn(
            state, user_text, TOOL_DESCRIPTIONS, execute_tool=self._tool_executor(state)
        )
        if response.text:
            state.messages.append(Message(role=Role.ASSISTANT, content=response.text))
        if response.is_final and state.step == ConversationStep.CONFIRMATION:
            state.step = ConversationStep.END
        return response

    async def _process_call(self, call_id: str) -> None:
        """Главный цикл обработки одного звонка. Жёстко ограничен call_timeout_seconds."""
        with structlog.contextvars.bound_contextvars(call_id=call_id):
            state = await self.state_store.get(call_id)
            if state is None:
                logger.error("call_state_missing")
                return
            try:
                async with asyncio.timeout(self.settings.call_timeout_seconds):
                    await self._dialog_loop(state)
            except TimeoutError:
                logger.warning("call_timeout", seconds=self.settings.call_timeout_seconds)
                state.step = ConversationStep.END
                await self._say_safe(state, "К сожалению, время звонка истекло. До свидания!")
            except Exception:
                logger.exception("call_failed")
            finally:
                await self._save(state)
                try:
                    await self.telephony.hangup(call_id)
                except Exception:
                    logger.warning("hangup_failed", exc_info=True)
                logger.info("call_completed", step=state.step.value)

    async def _dialog_loop(self, state: ConversationState) -> None:
        await self._say(
            state,
            f"Здравствуйте! Это {self.settings.salon_name}. Я AI-ассистент, помогу записаться на услугу.",
        )

        while state.step not in TERMINAL_STEPS:
            user_text = await self._listen(state)
            if not user_text:
                if state.retry_count >= self.settings.max_retries:
                    await self._say(state, "К сожалению, я вас не слышу. До свидания!")
                    state.step = ConversationStep.END
                    break
                state.retry_count += 1
                await self._say(state, "Алло, вы меня слышите?")
                continue

            state.retry_count = 0
            response = await self._run_turn(state, user_text)
            if response.text:
                await self._say(state, response.text)
            await self._save(state)

        if state.step == ConversationStep.ESCALATE:
            # ponytail: реального перевода звонка (transfer) в ABC телефонии нет —
            # добавить BaseTelephonyProvider.transfer() вместе с реальным провайдером.
            logger.info("call_escalated", reason=state.escalation_reason)

    async def _listen(self, state: ConversationState) -> str:
        """Получает реплику клиента.

        В mock-режиме — здесь нет реального аудио-стрима, поэтому voice-цикл
        для mock-телефонии не ведёт содержательный диалог (используйте
        /call/text для полноценного текстового прогона FSM).
        В реальном режиме — стримит аудио из telephony в STT (ждёт реализации
        провайдер-специфичного WebSocket/Media Streams моста).
        """
        if self.stt.name == "mock":
            await asyncio.sleep(0.5)
            return ""

        # Заглушка: в реальной реализации — чтение из очереди событий
        # и стриминг аудио в STT
        return ""

    async def _say(self, state: ConversationState, text: str) -> None:
        """Синтезирует речь и отправляет клиенту."""
        logger.debug("agent_says", text=text)

        async def audio_stream() -> Any:
            async for chunk in self.tts.synthesize_stream(
                text, language=state.language, sample_rate=self.settings.sample_rate
            ):
                yield chunk

        await self.telephony.send_audio(state.call_id, audio_stream())
        try:
            await self.telephony.send_text(state.call_id, text)
        except NotImplementedError:
            pass

    async def _say_safe(self, state: ConversationState, text: str) -> None:
        try:
            await self._say(state, text)
        except Exception:
            logger.warning("say_failed", exc_info=True)

    def _tool_executor(self, state: ConversationState) -> ToolExecutor:
        """Замыкание над state — передаётся LLM-агенту как execute_tool."""

        async def _execute(name: str, arguments: dict[str, Any]) -> Any:
            return await self._execute_tool(state, name, arguments)

        return _execute

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
                state.slot = booking.slot
                state.service = booking.service
                state.master = booking.master
                state.step = ConversationStep.CONFIRMATION
                return {"success": True, "booking_id": booking.id, "when": booking.slot.start_at.isoformat()}

            if name == "escalate_to_human":
                state.step = ConversationStep.ESCALATE
                state.escalation_reason = str(arguments.get("reason") or "unspecified")
                return {"success": True, "message": "Transferring to a human administrator."}

            msg = f"Unknown tool: {name}"
            return {"error": msg}

        except Exception as e:  # noqa: BLE001 — ошибка одного tool call не должна ронять весь turn
            logger.error("tool_execution_failed", tool=name, error=str(e))
            return {"error": str(e)}


async def build_orchestrator() -> Orchestrator:
    """Собирает все провайдеры и создаёт orchestrator."""
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


__all__ = ["DuplicateCallbackError", "Orchestrator", "build_orchestrator"]
