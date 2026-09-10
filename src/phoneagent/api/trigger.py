"""Trigger API — клиент нажал "Перезвонить" → POST /call/request-callback."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from phoneagent.api.security import require_api_token
from phoneagent.core.orchestrator import DuplicateCallbackError, Orchestrator
from phoneagent.utils import get_logger, mask_phone

router = APIRouter(prefix="/call", tags=["call"], dependencies=[Depends(require_api_token)])
logger = get_logger(__name__)

E164 = r"^\+[1-9]\d{7,14}$"


def _orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator  # type: ignore[no-any-return]


class CallbackRequest(BaseModel):
    """Запрос на обратный звонок."""

    phone: str = Field(..., pattern=E164, description="Номер клиента в E.164 (+79991234567)")
    salon_id: str = Field(default="demo", description="ID салона (v1: информационно, см. README)")
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
async def request_callback(req: CallbackRequest, request: Request) -> CallbackResponse:
    """Клиент нажал "Перезвонить" → AI звонит ему и ведёт диалог для записи.

    Этот эндпоинт вызывается с бэкенда сайта/приложения салона (server-to-server —
    Bearer-токен нельзя светить в браузерном JS).
    """
    try:
        orchestrator = _orchestrator(request)
        call_id = await orchestrator.handle_callback(
            client_phone=req.phone,
            salon_id=req.salon_id,
            language=req.language,
            announcement=req.extra.get("initial_message") or None,
        )
        logger.info("callback_requested", call_id=call_id, phone=mask_phone(req.phone))
        return CallbackResponse(call_id=call_id)
    except DuplicateCallbackError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A callback to this number was requested recently",
        )
    except RuntimeError as e:
        # Провайдер не поддерживает реальный звонок (см. Orchestrator.handle_callback) —
        # это ошибка конфигурации, а не внутренний сбой, поэтому 400 и текст безопасен.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        logger.exception("callback_failed", phone=mask_phone(req.phone))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate call",
        )


class TextMessageRequest(BaseModel):
    """Сообщение в текстовом режиме диалога."""

    text: str = Field(..., min_length=1, max_length=2000, description="Реплика клиента")
    call_id: str | None = Field(default=None, description="Продолжить существующий диалог")
    # Обязателен: агент исходит из того, что номер клиента известен и не спрашивает его.
    # Виджет должен запросить номер до начала чата.
    phone: str = Field(default="", description="Номер клиента E.164 (нужен для новой сессии)")
    salon_id: str = Field(default="demo", description="ID салона")
    language: str = Field(default="ru", description="Язык диалога")


class TextMessageResponse(BaseModel):
    """Ответ ассистента в текстовом режиме."""

    call_id: str
    reply: str
    step: str


@router.post(
    "/text",
    response_model=TextMessageResponse,
    summary="Текстовый режим (для отладки / чат-виджета)",
)
async def text_message(req: TextMessageRequest, request: Request) -> TextMessageResponse:
    """Отправить текстовое сообщение от клиента (без реального звонка).

    Полезно для разработки/отладки FSM и как бэкенд чат-виджета на сайте —
    гоняет тот же LLM-агент и booking connector, что и голосовой звонок,
    просто без telephony/STT/TTS.
    """
    import re

    if not req.call_id and not re.match(E164, req.phone):
        raise HTTPException(
            status_code=422,
            detail="phone (E.164) is required to start a new conversation",
        )

    orchestrator = _orchestrator(request)
    try:
        result = await orchestrator.handle_text_message(
            call_id=req.call_id,
            salon_id=req.salon_id,
            client_phone=req.phone,
            language=req.language,
            text=req.text,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return TextMessageResponse(**result)
