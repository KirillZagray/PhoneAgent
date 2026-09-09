"""Mock booking connector для разработки.

Возвращает фиксированные услуги и слоты для тестирования диалога.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from phoneagent.connectors.base import BaseBookingConnector
from phoneagent.models.booking import (
    Booking,
    BookingRequest,
    Master,
    Service,
    ServiceCategory,
    Slot,
)


class MockBookingConnector(BaseBookingConnector):
    """Имитация системы записи с фиксированным набором услуг и мастеров."""

    name = "mock"

    def __init__(self) -> None:
        self._services: list[Service] = [
            Service(
                id="haircut_women",
                name="Стрижка женская",
                category=ServiceCategory.HAIR,
                duration_minutes=60,
                price=2500.0,
                description="Стрижка любой сложности с мытьём головы",
            ),
            Service(
                id="haircut_men",
                name="Стрижка мужская",
                category=ServiceCategory.HAIR,
                duration_minutes=45,
                price=1500.0,
                description="Классическая мужская стрижка",
            ),
            Service(
                id="coloring",
                name="Окрашивание",
                category=ServiceCategory.COLOR,
                duration_minutes=120,
                price=5000.0,
                description="Окрашивание в один тон / мелирование",
            ),
            Service(
                id="manicure",
                name="Маникюр",
                category=ServiceCategory.NAILS,
                duration_minutes=60,
                price=2000.0,
            ),
            Service(
                id="pedicure",
                name="Педикюр",
                category=ServiceCategory.NAILS,
                duration_minutes=75,
                price=2500.0,
            ),
            Service(
                id="brows",
                name="Коррекция бровей",
                category=ServiceCategory.BROWS,
                duration_minutes=30,
                price=800.0,
            ),
        ]
        self._masters: list[Master] = [
            Master(
                id="m1",
                name="Анна",
                specialization=[ServiceCategory.HAIR, ServiceCategory.COLOR],
                rating=4.9,
                description="Топ-мастер, 10 лет опыта",
            ),
            Master(
                id="m2",
                name="Мария",
                specialization=[ServiceCategory.HAIR, ServiceCategory.NAILS],
                rating=4.8,
            ),
            Master(
                id="m3",
                name="Елена",
                specialization=[ServiceCategory.NAILS, ServiceCategory.BROWS],
                rating=4.7,
            ),
            Master(
                id="m4",
                name="Ольга",
                specialization=[ServiceCategory.BROWS],
                rating=4.9,
            ),
        ]
        self._bookings: dict[str, Booking] = {}

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def list_services(self) -> list[Service]:
        return list(self._services)

    async def list_masters(self, service_id: str | None = None) -> list[Master]:
        if service_id is None:
            return list(self._masters)
        service = next((s for s in self._services if s.id == service_id), None)
        if service is None:
            return []
        return [m for m in self._masters if service.category in m.specialization]

    async def list_slots(
        self,
        master_id: str,
        target_date: date,
        service_id: str | None = None,
    ) -> list[Slot]:
        # Генерируем слоты с 10:00 до 19:00 каждый час
        slots: list[Slot] = []
        service_duration = 60
        if service_id:
            service = next((s for s in self._services if s.id == service_id), None)
            if service:
                service_duration = service.duration_minutes

        for hour in range(10, 19):
            start = datetime.combine(target_date, datetime.min.time()).replace(hour=hour)
            slots.append(
                Slot(
                    id=f"{master_id}-{target_date.isoformat()}-{hour:02d}00",
                    master_id=master_id,
                    service_id=service_id,
                    start_at=start,
                    duration_minutes=service_duration,
                    available=True,
                )
            )
        return slots

    async def create_booking(self, request: BookingRequest) -> Booking:
        slot_id = f"{request.master_id}-{request.date.isoformat()}-{request.time.replace(':', '')}"
        service = next((s for s in self._services if s.id == request.service_id), None)
        master = next((m for m in self._masters if m.id == request.master_id), None)

        if service is None or master is None:
            msg = f"Service or master not found: {request}"
            raise ValueError(msg)

        slot = Slot(
            id=slot_id,
            master_id=request.master_id,
            service_id=request.service_id,
            start_at=datetime.combine(request.date, datetime.min.time()).replace(
                hour=int(request.time.split(":")[0]),
                minute=int(request.time.split(":")[1]),
            ),
            duration_minutes=service.duration_minutes,
            available=False,
        )
        booking = Booking(
            id=f"booking-{len(self._bookings) + 1}",
            salon_id="demo",
            service=service,
            master=master,
            slot=slot,
            client_phone=request.client_phone,
            client_name=request.client_name,
            notes="Запись создана через PhoneAgent",
        )
        self._bookings[booking.id] = booking
        return booking

    async def get_booking(self, booking_id: str) -> Booking | None:
        return self._bookings.get(booking_id)