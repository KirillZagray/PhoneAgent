"""Модели бронирований."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ServiceCategory(StrEnum):
    HAIR = "hair"                    # Стрижка, укладка
    COLOR = "color"                  # Окрашивание
    NAILS = "nails"                  # Маникюр, педикюр
    BROWS = "brows"                  # Брови, ресницы
    COSMETOLOGY = "cosmetology"      # Косметология
    MASSAGE = "massage"              # Массаж
    EPILATION = "epilation"          # Эпиляция
    OTHER = "other"


class Service(BaseModel):
    """Услуга салона."""

    model_config = ConfigDict(frozen=False)

    id: str
    name: str
    category: ServiceCategory = ServiceCategory.OTHER
    duration_minutes: int = Field(..., gt=0)
    price: float | None = None
    description: str | None = None


class Master(BaseModel):
    """Мастер / специалист."""

    model_config = ConfigDict(frozen=False)

    id: str
    name: str
    specialization: list[ServiceCategory] = Field(default_factory=list)
    rating: float | None = None
    description: str | None = None


class Slot(BaseModel):
    """Свободный временной слот."""

    model_config = ConfigDict(frozen=False)

    id: str
    master_id: str
    service_id: str | None = None
    start_at: datetime
    duration_minutes: int = Field(..., gt=0)
    available: bool = True


class Booking(BaseModel):
    """Подтверждённая запись."""

    model_config = ConfigDict(frozen=False)

    id: str
    salon_id: str
    service: Service
    master: Master
    slot: Slot
    client_phone: str
    client_name: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    notes: str | None = None


class BookingRequest(BaseModel):
    """Запрос на создание бронирования."""

    service_id: str
    master_id: str
    date: date
    time: str = Field(..., description="HH:MM")
    client_phone: str
    client_name: str | None = None
