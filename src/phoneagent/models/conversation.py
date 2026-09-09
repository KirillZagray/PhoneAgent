"""Модели диалога (FSM)."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"           # Клиент (распознанный текст)
    ASSISTANT = "assistant" # AI-агент
    TOOL = "tool"           # Результат tool call


class ConversationStep(StrEnum):
    """Состояния FSM диалога."""

    GREETING = "greeting"
    ASK_SERVICE = "ask_service"
    ASK_DATE = "ask_date"
    ASK_MASTER = "ask_master"
    OFFER_SLOTS = "offer_slots"
    CONFIRM = "confirm"
    CREATE_BOOKING = "create_booking"
    CONFIRMATION = "confirmation"
    END = "end"
    ESCALATE = "escalate"


class Message(BaseModel):
    """Одно сообщение в диалоге."""

    model_config = ConfigDict(extra="allow")

    role: Role
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ConversationState(BaseModel):
    """Полное состояние диалога с клиентом."""

    model_config = ConfigDict(extra="allow")

    call_id: str
    salon_id: str
    client_phone: str
    language: str = "ru"

    # FSM
    step: ConversationStep = ConversationStep.GREETING
    retry_count: int = 0

    # Собранные данные
    service: Service | None = None  # type: ignore[name-defined]  # noqa: F821
    master: Master | None = None  # type: ignore[name-defined]  # noqa: F821
    slot: Slot | None = None  # type: ignore[name-defined]  # noqa: F821
    desired_date: date | None = None
    desired_time: str | None = None  # HH:MM
    client_name: str | None = None

    # История
    messages: list[Message] = Field(default_factory=list)

    # Служебное
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    escalation_reason: str | None = None
