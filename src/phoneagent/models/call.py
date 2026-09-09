"""Модели звонков."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CallStatusEnum(StrEnum):
    """Статусы телефонного звонка."""

    PENDING = "pending"          # Создан, ожидает провайдера
    INITIATED = "initiated"      # Провайдер начал вызов
    RINGING = "ringing"          # Идёт дозвон
    CONNECTED = "connected"      # Клиент поднял трубку
    COMPLETED = "completed"      # Разговор завершён успешно
    FAILED = "failed"            # Ошибка (не дозвонились)
    NO_ANSWER = "no_answer"      # Не ответили
    BUSY = "busy"                # Занято
    CANCELED = "canceled"        # Отменено нашей стороной
    TIMEOUT = "timeout"          # Превышен таймаут


class CallRef(BaseModel):
    """Ссылка на звонок у провайдера телефонии."""

    model_config = ConfigDict(frozen=False)

    call_id: str = Field(..., description="ID звонка у провайдера")
    provider: str = Field(..., description="Имя провайдера (voximplant/twilio/...)")
    phone: str = Field(..., description="Номер клиента (E.164)")
    status: CallStatusEnum = CallStatusEnum.PENDING
    metadata: dict[str, str] = Field(default_factory=dict)


class CallStatus(BaseModel):
    """Текущий статус звонка."""

    call_id: str
    status: CallStatusEnum
    started_at: datetime | None = None
    connected_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int = 0
    error: str | None = None


class CallEvent(BaseModel):
    """Событие от провайдера (webhook / WebSocket)."""

    model_config = ConfigDict(extra="allow")

    event_type: Literal["started", "connected", "audio", "dtmf", "ended", "error"]
    call_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
