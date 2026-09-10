"""Аудио-мост VoxEngine <-> PhoneAgent.

Phase B: реальная передача аудио, не эхо. Мост не знает про STT/LLM/TTS —
он просто переливает байты между VoxEngine и общей `AudioSession` для этого
call_id (см. core/audio_session.py); вся логика диалога — в Orchestrator.

Протокол — JSON-текстовые фреймы (не бинарный WS!), симметричный в обе
стороны, проверен по актуальным докам Voximplant:

    {"event": "start", "sequenceNumber": 0,
     "start": {"mediaFormat": {...}, "customParameters": {...}}}
    {"event": "media", "sequenceNumber": N,
     "media": {"chunk": N, "timestamp": T, "payload": "<base64 PCM>"}}
    {"event": "stop", "sequenceNumber": N, "stop": {"mediaInfo": {...}}}

Авторизация — тот же общий секрет, что и у /webhooks/* (`WEBHOOK_SECRET`),
только в заголовке `X-Webhook-Secret` при апгрейде WS-соединения (FastAPI
Depends() на HTTP-заголовки для WS-роутов не работает как для обычных путей
до `accept()`, поэтому проверяем вручную — см. `secret_matches`).
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from phoneagent.api.security import secret_matches
from phoneagent.config import PhoneAgentSettings, get_settings
from phoneagent.core.audio_session import AudioSession, create_session, paced_frames, remove_session
from phoneagent.utils import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Код закрытия для отказа в авторизации. 4000-4999 — приватный диапазон по RFC 6455.
WS_UNAUTHORIZED = 4401


async def _watch_hangup(websocket: WebSocket, session: AudioSession) -> None:
    """Ждёт session.hangup_requested (Orchestrator закончил разговор) и
    закрывает WS — это единственный способ сказать VoxEngine-сценарию
    "вешай трубку" для входящего звонка (см. AudioSession.hangup_requested).
    Закрытие WS будит основной receive-цикл через WebSocketDisconnect.
    """
    await session.hangup_requested.wait()
    logger.info("voxengine_bridge_hangup_requested", call_id=session.call_id)
    try:
        await websocket.close()
    except RuntimeError:
        pass  # уже закрыт


async def _send_outgoing_audio(
    websocket: WebSocket, session: AudioSession, settings: PhoneAgentSettings
) -> None:
    """Вычитывает AudioSession.outgoing (TTS-ответы Orchestrator) и шлёт в
    VoxEngine кадрами фиксированного размера с реальным таймингом — см.
    paced_frames про то, зачем это нужно (VoxEngine сам не темпирует).
    """
    hangup_watcher = asyncio.create_task(_watch_hangup(websocket, session))
    sequence = 0
    try:
        async for frame in paced_frames(
            session, frame_ms=settings.audio_frame_ms, sample_rate=settings.sample_rate
        ):
            sequence += 1
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "media",
                        "sequenceNumber": sequence,
                        "media": {
                            "chunk": sequence,
                            "timestamp": sequence * settings.audio_frame_ms,
                            "payload": base64.b64encode(frame).decode(),
                        },
                    }
                )
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("voxengine_bridge_sender_crashed", call_id=session.call_id)
    finally:
        hangup_watcher.cancel()


@router.websocket("/ws/voxengine/{call_id}")
async def voxengine_media_bridge(websocket: WebSocket, call_id: str) -> None:
    settings = get_settings()
    secret = settings.webhook_secret
    if secret and not secret_matches(websocket.headers.get("x-webhook-secret"), secret):
        await websocket.close(code=WS_UNAUTHORIZED)
        return

    await websocket.accept()
    logger.info("voxengine_bridge_connected", call_id=call_id)

    session = create_session(call_id)
    sender_task: asyncio.Task[None] | None = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("voxengine_bridge_bad_json", call_id=call_id)
                continue

            event = message.get("event")
            if event == "start":
                start = message.get("start", {})
                logger.info(
                    "voxengine_bridge_start",
                    call_id=call_id,
                    media_format=start.get("mediaFormat"),
                    custom_parameters=start.get("customParameters"),
                )
                # Протокол симметричный ("it works both ways" — доки
                # Voximplant): отвечаем своим "start", как только узнали их.
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "start",
                            "sequenceNumber": 0,
                            "start": {
                                "mediaFormat": {
                                    "encoding": "PCM16",
                                    "sampleRate": settings.sample_rate,
                                    "channels": 1,
                                }
                            },
                        }
                    )
                )
                # Sender-таска стартует только теперь — не раньше, чем
                # ушёл наш "start". Раньше она бежала параллельно с самого
                # connect() и на втором тестовом звонке успевала отправить
                # "media" впереди "start" (гонка, см. отладку 2026-09-10) —
                # либо вообще ничего не срабатывало, VoxEngine молчал.
                if sender_task is None:
                    sender_task = asyncio.create_task(
                        _send_outgoing_audio(websocket, session, settings)
                    )
                orchestrator = websocket.app.state.orchestrator
                if await orchestrator.state_store.get(call_id) is None:
                    # Состояния ещё нет — значит звонок входящий (исходящий
                    # получает его от handle_callback ДО того, как сценарий
                    # успевает открыть этот WS). Номер звонящего сценарий
                    # передаёт заголовком при апгрейде — customParameters
                    # StartScenarios тут не участвует.
                    client_phone = websocket.headers.get("x-client-phone") or "unknown"
                    asyncio.create_task(
                        orchestrator.handle_inbound_call(call_id, settings.salon_id, client_phone)
                    )
            elif event == "media":
                payload_b64 = message.get("media", {}).get("payload", "")
                if payload_b64:
                    session.push_incoming(base64.b64decode(payload_b64))
            elif event == "stop":
                logger.info("voxengine_bridge_stop", call_id=call_id)
                break
            else:
                logger.debug("voxengine_bridge_unknown_event", call_id=call_id, event_name=event)
    except WebSocketDisconnect:
        logger.info("voxengine_bridge_disconnected", call_id=call_id)
    finally:
        if sender_task is not None:
            sender_task.cancel()
        remove_session(call_id)
        try:
            await websocket.close()
        except RuntimeError:
            pass  # уже закрыт (например, после WebSocketDisconnect)


__all__ = ["router"]
