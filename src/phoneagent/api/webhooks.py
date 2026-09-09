"""Webhooks от провайдеров телефонии."""

from __future__ import annotations

from fastapi import APIRouter, Request

from phoneagent.utils import get_logger

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)


@router.post("/voximplant", summary="Webhook от Voximplant")
async def voximplant_webhook(request: Request) -> dict[str, str]:
    """Обрабатывает события звонков от Voximplant."""
    payload = await request.json()
    logger.info("voximplant_webhook", payload=payload)
    # TODO: переслать в orchestrator через event queue
    return {"status": "ok"}


@router.post("/twilio", summary="Webhook от Twilio")
async def twilio_webhook(request: Request) -> dict[str, str]:
    """Обрабатывает события звонков от Twilio."""
    payload = await request.json()
    logger.info("twilio_webhook", payload=payload)
    return {"status": "ok"}


@router.post("/asterisk", summary="Webhook от Asterisk ARI")
async def asterisk_webhook(request: Request) -> dict[str, str]:
    """Обрабатывает события от Asterisk ARI."""
    payload = await request.json()
    logger.info("asterisk_webhook", payload=payload)
    return {"status": "ok"}