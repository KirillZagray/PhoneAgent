"""Аудио-мост VoxEngine <-> PhoneAgent.

Phase A (текущая реализация): чистое эхо. Цель — подтвердить, что транспорт
и формат сообщений реально работают (VoxEngine -> сюда -> обратно в трубку),
прежде чем подключать STT/LLM/TTS. См.
docs/superpowers/specs/2026-09-09-voximplant-audio-bridge-design.md.

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

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from phoneagent.api.security import secret_matches
from phoneagent.config import get_settings
from phoneagent.utils import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Код закрытия для отказа в авторизации. 4000-4999 — приватный диапазон по RFC 6455.
WS_UNAUTHORIZED = 4401


@router.websocket("/ws/voxengine/{call_id}")
async def voxengine_media_bridge(websocket: WebSocket, call_id: str) -> None:
    settings = get_settings()
    secret = settings.webhook_secret
    if secret and not secret_matches(websocket.headers.get("x-webhook-secret"), secret):
        await websocket.close(code=WS_UNAUTHORIZED)
        return

    await websocket.accept()
    logger.info("voxengine_bridge_connected", call_id=call_id)

    sequence = 0
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
            elif event == "media":
                media = message.get("media", {})
                sequence += 1
                # Phase A: эхо — тот же payload обратно неизменным.
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "sequenceNumber": sequence,
                            "media": {
                                "chunk": media.get("chunk", 0),
                                "timestamp": media.get("timestamp", 0),
                                "payload": media.get("payload", ""),
                            },
                        }
                    )
                )
            elif event == "stop":
                logger.info("voxengine_bridge_stop", call_id=call_id)
                break
            else:
                logger.debug("voxengine_bridge_unknown_event", call_id=call_id, event_name=event)
    except WebSocketDisconnect:
        logger.info("voxengine_bridge_disconnected", call_id=call_id)
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass  # уже закрыт (например, после WebSocketDisconnect)


__all__ = ["router"]
