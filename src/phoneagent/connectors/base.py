"""Базовый интерфейс booking connector."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from phoneagent.models.booking import (
    Booking,
    BookingRequest,
    Master,
    Service,
    Slot,
)


class BaseBookingConnector(ABC):
    """Абстракция для интеграции с системой записи салона.

    Каждый адаптер (GenericAPI, YClients, Dikidi, Altegio) реализует этот интерфейс.
    """

    name: str = "base"

    @abstractmethod
    async def connect(self) -> None:
        """Инициализация клиента."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Закрытие клиента."""
        ...

    @abstractmethod
    async def list_services(self) -> list[Service]:
        """Возвращает список услуг салона."""
        ...

    @abstractmethod
    async def list_masters(self, service_id: str | None = None) -> list[Master]:
        """Возвращает список мастеров (опционально фильтр по услуге)."""
        ...

    @abstractmethod
    async def list_slots(
        self,
        master_id: str,
        target_date: date,
        service_id: str | None = None,
    ) -> list[Slot]:
        """Возвращает свободные слоты мастера на дату."""
        ...

    @abstractmethod
    async def create_booking(self, request: BookingRequest) -> Booking:
        """Создаёт запись. Возвращает подтверждённый Booking."""
        ...

    @abstractmethod
    async def get_booking(self, booking_id: str) -> Booking | None:
        """Получить запись по ID (для верификации)."""
        ...

    async def __aenter__(self) -> "BaseBookingConnector":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.disconnect()