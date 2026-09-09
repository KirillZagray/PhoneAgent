"""Webhooks от провайдеров телефонии.

Пока только принимают и логируют (этап 2 roadmap — прокинуть в orchestrator
через очередь событий per call_id). Полный payload — только на DEBUG: в нём
номера, записи, транскрипты.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from phoneagent.api.security import require_webhook_secret
from phoneagent.utils import get_logger

router = APIRouter(prefix="/webhooks", tags=["webhooks"], dependencies=[Depends(require_webhook_secret)])
logger = get_logger(__name__)


async def _receive(request: Request, provider: str) -> dict[str, str]:
    payload: Any = await request.json()
    keys = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
    logger.info("webhook_received", provider=provider, keys=keys)
    logger.debug("webhook_payload", provider=provider, payload=payload)
    # TODO(этап 2): переслать в orchestrator через event queue
    return {"status": "ok"}


@router.post("/voximplant", summary="Webhook от Voximplant")
async def voximplant_webhook(request: Request) -> dict[str, str]:
    return await _receive(request, "voximplant")


@router.post("/twilio", summary="Webhook от Twilio")
async def twilio_webhook(request: Request) -> dict[str, str]:
    return await _receive(request, "twilio")


@router.post("/asterisk", summary="Webhook от Asterisk ARI")
async def asterisk_webhook(request: Request) -> dict[str, str]:
    return await _receive(request, "asterisk")
