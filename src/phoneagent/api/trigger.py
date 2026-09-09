"""Trigger API — клиент нажал "Перезвонить" → POST /call/request-callback."""

from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException, status

from phoneagent.core.orchestrator import build_orchestrator
from phoneagent.utils import get_logger

router = APIRouter(prefix="/call", tags=["call"])
logger = get_logger(__name__)


class CallbackRequest(BaseModel):
    """Запрос на обратный звонок."""

    phone: str = Field(..., description="Номер клиента в E.164 (+79991234567)")
    salon_id: str = Field(default="demo", description="ID салона")
    language: str = Field(default="ru", description="Язык диалога")
    extra: dict[str, str] = Field(default_factory=dict, description="Доп. метаданные")


class CallbackResponse(BaseModel):
    """Ответ на запрос обратного звонка."""

    call_id: str
    status: str = "initiated"


@router.post(
    "/request-callback",
    response_model=CallbackResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Инициировать обратный звонок клиенту",
)
async def request_callback(req: CallbackRequest) -> CallbackResponse:
    """Клиент нажал "Перезвонить" → AI звонит ему и ведёт диалог для записи.

    Этот эндпоинт вызывается с сайта/приложения салона.
    """
    try:
        orchestrator = await build_orchestrator()
        call_id = await orchestrator.handle_callback(
            client_phone=req.phone,
            salon_id=req.salon_id,
            language=req.language,
        )
        logger.info("callback_requested", call_id=call_id, phone=req.phone)
        return CallbackResponse(call_id=call_id)
    except Exception as e:
        logger.error("callback_failed", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate call: {e!s}",
        )


@router.post(
    "/text",
    summary="Текстовый режим (для отладки / чат-виджета)",
)
async def text_message(req: CallbackRequest) -> dict[str, str]:
    """Отправить текстовое сообщение от клиента (без реального звонка).

    Полезно для:
    - Разработки и отладки
    - Чат-виджета на сайте
    - Тестов
    """
    # В текстовом режиме работаем с тем же оркестратором
    # без telephony провайдера
    return {
        "status": "ok",
        "message": "Text mode — реализация в разработке",
    }