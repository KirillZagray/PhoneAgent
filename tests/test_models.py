"""Unit-тесты моделей."""

from datetime import date, datetime

from phoneagent.models.booking import (
    BookingRequest,
    Master,
    Service,
    ServiceCategory,
    Slot,
)
from phoneagent.models.call import CallEvent, CallRef, CallStatusEnum
from phoneagent.models.conversation import (
    ConversationState,
    ConversationStep,
    Message,
    Role,
)


def test_service_creation() -> None:
    service = Service(
        id="haircut",
        name="Стрижка",
        duration_minutes=60,
        price=2500.0,
    )
    assert service.id == "haircut"
    assert service.duration_minutes == 60
    assert service.category == ServiceCategory.OTHER


def test_master_with_specialization() -> None:
    master = Master(
        id="m1",
        name="Анна",
        specialization=[ServiceCategory.HAIR, ServiceCategory.COLOR],
        rating=4.9,
    )
    assert "Анна" in master.name
    assert ServiceCategory.HAIR in master.specialization


def test_slot_creation() -> None:
    slot = Slot(
        id="s1",
        master_id="m1",
        start_at=datetime(2026, 3, 15, 14, 0),
        duration_minutes=60,
    )
    assert slot.available is True
    assert slot.start_at.hour == 14


def test_call_ref() -> None:
    ref = CallRef(
        call_id="abc123",
        provider="voximplant",
        phone="+79991234567",
    )
    assert ref.status == CallStatusEnum.PENDING
    assert ref.provider == "voximplant"


def test_call_event() -> None:
    event = CallEvent(event_type="connected", call_id="abc123")
    assert event.event_type == "connected"


def test_conversation_state() -> None:
    state = ConversationState(
        call_id="abc123",
        salon_id="salon1",
        client_phone="+79991234567",
    )
    assert state.step == ConversationStep.GREETING
    assert state.retry_count == 0
    assert state.messages == []


def test_message_in_conversation() -> None:
    state = ConversationState(
        call_id="abc123",
        salon_id="salon1",
        client_phone="+79991234567",
    )
    state.messages.append(Message(role=Role.USER, content="Хочу записаться"))
    assert len(state.messages) == 1
    assert state.messages[0].role == Role.USER


def test_booking_request() -> None:
    req = BookingRequest(
        service_id="haircut",
        master_id="m1",
        date=date(2026, 3, 15),
        time="14:00",
        client_phone="+79991234567",
    )
    assert req.time == "14:00"
    assert req.date.year == 2026