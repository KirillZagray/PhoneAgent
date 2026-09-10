"""Orchestrator — главный цикл звонка.

Связывает Telephony + STT + LLM + TTS + Booking.
"""

from __future__ import annotations

import asyncio
import audioop
import contextlib
import random
import uuid
from datetime import date, datetime
from typing import Any

import structlog

from phoneagent.config import get_settings
from phoneagent.connectors.base import BaseBookingConnector
from phoneagent.connectors.factory import build_booking_connector
from phoneagent.core.agent import (
    TOOL_DESCRIPTIONS,
    AgentResponse,
    BaseLLMAgent,
    ToolExecutor,
    build_llm_agent,
)
from phoneagent.core.audio_session import AudioSession, get_session
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

# Филлер-фразы на случай, если LLM отвечает дольше llm_soft_timeout_seconds —
# тишина в трубке дольше пары секунд ощущается как оборвавшийся звонок.
FILLER_PHRASES = (
    "Секунду, уточняю...",
    "Одну минуту, смотрю...",
    "Сейчас проверю...",
)


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
        announcement: str | None = None,
    ) -> str:
        """Точка входа: клиент нажал "Перезвонить" → инициируем звонок.

        announcement: если задано — это не запись на услугу, а разовое
        голосовое уведомление (см. ConversationState.announcement и
        _dialog_loop) — звонок проговаривает текст и сразу завершается.

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
            announcement=announcement,
        )
        await self._save(state)

        # 3. Запускаем обработку звонка в фоне (ссылку держим в self._background_tasks)
        task = asyncio.create_task(self._process_call(call_ref.call_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return call_ref.call_id

    async def handle_inbound_call(
        self,
        call_id: str,
        salon_id: str,
        client_phone: str,
        *,
        language: str | None = None,
    ) -> None:
        """Точка входа для ВХОДЯЩЕГО звонка: клиент дозвонился сам.

        В отличие от handle_callback — телефония уже держит живой звонок
        (мост уже подключён, AudioSession уже создана в media_ws.py/
        audiosocket_server.py до вызова этого метода), поэтому тут нет ни
        make_call(), ни dedupe-лока, ни проверки supports_realtime_audio.
        Вызывается мостом на событии "start", когда для call_id ещё нет
        состояния в state_store (см. api/media_ws.py).
        """
        language = language or self.settings.default_language

        state = ConversationState(
            call_id=call_id,
            salon_id=salon_id,
            client_phone=client_phone,
            language=language,
            step=ConversationStep.GREETING,
        )
        await self._save(state)
        logger.info("inbound_call_started", call_id=call_id, phone=mask_phone(client_phone))

        task = asyncio.create_task(self._process_call(call_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

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

    async def _run_turn(self, state: ConversationState, user_text: str) -> AgentResponse:
        """Один turn: реплика клиента → LLM (+tools) → текст ответа. Двигает FSM.

        Мягкий таймаут (llm_soft_timeout_seconds): если модель молчит дольше
        — говорим филлер-фразу и продолжаем ждать настоящий ответ тем же
        запросом (asyncio.shield — не отменяем его). Любая ошибка LLM
        (быстрый сбой API или обрыв уже после филлера) — не роняем звонок
        молча, а вежливо прощаемся и завершаем разговор.
        """
        state.messages.append(Message(role=Role.USER, content=user_text))
        llm_task = asyncio.create_task(
            self.llm.run_turn(
                state, user_text, TOOL_DESCRIPTIONS, execute_tool=self._tool_executor(state)
            )
        )
        try:
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(llm_task), timeout=self.settings.llm_soft_timeout_seconds
                )
            except TimeoutError:
                await self._say_safe(state, random.choice(FILLER_PHRASES))
                response = await llm_task
        except Exception:
            logger.exception("llm_turn_failed")
            state.step = ConversationStep.END
            await self._say_safe(
                state, "Извините, у меня технические неполадки. Пожалуйста, перезвоните позже."
            )
            return AgentResponse(text="", is_final=True)

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
        if state.announcement:
            # Разовое уведомление — не заводим FSM записи на услугу, просто
            # проговариваем текст и вешаем трубку (hangup — в _process_call).
            await self._say(state, state.announcement)
            state.step = ConversationStep.END
            return

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

        В реальном режиме (Phase B) — копит аудио из AudioSession, пока
        клиент говорит, до паузы (см. `_collect_utterance`), и отдаёт
        накопленное в STT целиком. Барж-ин (клиент перебивает ещё
        говорящего агента) обрабатывается внутри _say() — см. там.
        """
        if self.stt.name == "mock":
            await asyncio.sleep(0.5)
            return ""

        session = get_session(state.call_id)
        if session is None:
            logger.warning("listen_no_audio_session", call_id=state.call_id)
            return ""

        audio = await self._collect_utterance(session)
        if not audio:
            return ""

        return await self.stt.transcribe(
            audio, language=state.language, sample_rate=self.settings.sample_rate
        )

    async def _collect_utterance(self, session: AudioSession) -> bytes:
        """Простой energy-based VAD: копит входящее аудио, пока RMS-амплитуда
        выше порога, до тишины нужной длины после начала речи (см. настройки
        vad_* в config.py — там же обоснование и upgrade-путь).
        """
        settings = self.settings
        loop = asyncio.get_event_loop()
        start_time = loop.time()
        last_speech_time = start_time
        speech_started = False
        buffer = bytearray()

        while True:
            now = loop.time()
            if now - start_time >= settings.vad_max_utterance_seconds:
                break
            if speech_started:
                timeout = settings.vad_silence_seconds - (now - last_speech_time)
            else:
                timeout = settings.vad_initial_silence_seconds - (now - start_time)
            if timeout <= 0:
                break

            try:
                async with asyncio.timeout(timeout):
                    chunk = await session.incoming.get()
            except TimeoutError:
                break
            if chunk is None:  # сессия закрылась (звонок оборвался посреди реплики)
                break

            buffer.extend(chunk)
            if audioop.rms(chunk, 2) > settings.vad_energy_threshold:
                speech_started = True
                last_speech_time = loop.time()

        return bytes(buffer) if speech_started else b""

    async def _say(self, state: ConversationState, text: str) -> None:
        """Синтезирует речь и отправляет клиенту.

        Барж-ин: пока TTS играет, параллельно слушаем входящее аудио этой
        сессии (_wait_for_barge_in). Если клиент начал устойчиво говорить
        поверх агента — агент обрывает фразу (session.clear_outgoing +
        отмена send_audio) вместо того, чтобы доболтать до конца поверх
        собеседника (см. разбор реальной записи звонка 2026-09-10, где
        агент дважды продолжал говорить, пока клиент договаривал/повторял
        свою реплику). Всё, что успели вычитать из session.incoming за
        время речи агента, возвращаем обратно в очередь в конце — иначе
        речь клиента параллельно с агентом (даже не переросшая в барж-ин)
        молча терялась бы вместо того, чтобы достаться следующему _listen().

        Echo guard: если барж-ина не было, send_audio() не возвращается,
        пока мост реально не заберёт всё аудио на отправку (см.
        AudioSession.wait_drained), но короткий хвост эха/реверберации ещё
        может звучать на линии секунду после этого. Дополнительная пауза
        (echo_guard_seconds) перед тем, как _listen() начнёт слушать,
        снижает шанс, что VAD примет этот хвост за реплику клиента.
        """
        logger.debug("agent_says", text=text)

        async def audio_stream() -> Any:
            async for chunk in self.tts.synthesize_stream(
                text, language=state.language, sample_rate=self.settings.sample_rate
            ):
                yield chunk

        session = get_session(state.call_id)
        send_task = asyncio.create_task(self.telephony.send_audio(state.call_id, audio_stream()))

        if session is not None:
            collected: list[bytes] = []
            barge_in_task = asyncio.create_task(self._wait_for_barge_in(session, collected))
            done, _pending = await asyncio.wait(
                {send_task, barge_in_task}, return_when=asyncio.FIRST_COMPLETED
            )
            barged_in = barge_in_task in done and not send_task.done() and barge_in_task.result()
            if not barge_in_task.done():
                barge_in_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await barge_in_task
            for chunk in collected:
                session.push_incoming(chunk)
            if barged_in:
                logger.info("barge_in_detected", call_id=state.call_id)
                session.clear_outgoing()
                send_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await send_task
                return
            await send_task
        else:
            await send_task

        await asyncio.sleep(self.settings.echo_guard_seconds)
        try:
            await self.telephony.send_text(state.call_id, text)
        except NotImplementedError:
            pass

    async def _wait_for_barge_in(self, session: AudioSession, collected: list[bytes]) -> bool:
        """Слушает session.incoming, пока говорит агент. Каждый прочитанный
        чанк складывает в `collected` (общий буфер с вызывающим _say) —
        чтобы вызывающий код мог вернуть их в очередь, если барж-ин не
        подтвердится (или таск отменят раньше).

        Игнорирует первые barge_in_grace_seconds (хвост собственного эха
        сразу после начала фразы) и требует barge_in_confirm_chunks подряд
        идущих громких чанков — без этого один случайный щелчок/эхо-всплеск
        обрывал бы агента на ровном месте.
        """
        settings = self.settings
        loop = asyncio.get_event_loop()
        started_at = loop.time()
        consecutive = 0
        while True:
            chunk = await session.incoming.get()
            if chunk is None:
                return False
            collected.append(chunk)
            if loop.time() - started_at < settings.barge_in_grace_seconds:
                continue
            if audioop.rms(chunk, 2) > settings.vad_energy_threshold:
                consecutive += 1
                if consecutive >= settings.barge_in_confirm_chunks:
                    return True
            else:
                consecutive = 0

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
