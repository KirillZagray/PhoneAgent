"""Тесты оркестратора и booking connector."""

from datetime import date

import pytest

from phoneagent.connectors.mock import MockBookingConnector
from phoneagent.core.agent import MockLLMAgent
from phoneagent.models.booking import BookingRequest, ServiceCategory


@pytest.mark.asyncio
async def test_mock_booking_list_services() -> None:
    connector = MockBookingConnector()
    async with connector:
        services = await connector.list_services()
        assert len(services) > 0
        assert any(s.category == ServiceCategory.HAIR for s in services)


@pytest.mark.asyncio
async def test_mock_booking_list_masters_filtered() -> None:
    connector = MockBookingConnector()
    async with connector:
        all_masters = await connector.list_masters()
        assert len(all_masters) == 4

        # Фильтр по услуге маникюра
        masters = await connector.list_masters(service_id="manicure")
        assert all(ServiceCategory.NAILS in m.specialization for m in masters)


@pytest.mark.asyncio
async def test_mock_booking_list_slots() -> None:
    connector = MockBookingConnector()
    async with connector:
        slots = await connector.list_slots(
            master_id="m1",
            target_date=date(2026, 3, 15),
        )
        # Должно быть 9 слотов (10:00 - 18:00)
        assert len(slots) == 9
        assert slots[0].start_at.hour == 10
        assert slots[-1].start_at.hour == 18


@pytest.mark.asyncio
async def test_mock_booking_create_booking() -> None:
    connector = MockBookingConnector()
    async with connector:
        req = BookingRequest(
            service_id="haircut_women",
            master_id="m1",
            date=date(2026, 3, 15),
            time="14:00",
            client_phone="+79991234567",
        )
        booking = await connector.create_booking(req)
        assert booking.id.startswith("booking-")
        assert booking.salon_id == "demo"
        assert booking.client_phone == "+79991234567"
        assert booking.slot.start_at.hour == 14


@pytest.mark.asyncio
async def test_mock_llm_agent_greeting() -> None:
    from phoneagent.models.conversation import (
        ConversationState,
        ConversationStep,
        Role,
    )
    agent = MockLLMAgent()
    await agent.connect()
    try:
        state = ConversationState(
            call_id="test",
            salon_id="demo",
            client_phone="+79991234567",
        )
        response = await agent.run_turn(state, "Здравствуйте", tools=[])
        assert response.text != ""
        assert "AI" in response.text or "салон" in response.text.lower()
    finally:
        await agent.disconnect()


@pytest.mark.asyncio
async def test_mock_llm_agent_service_identification() -> None:
    from phoneagent.models.conversation import (
        ConversationState,
        ConversationStep,
    )
    agent = MockLLMAgent()
    await agent.connect()
    try:
        state = ConversationState(
            call_id="test",
            salon_id="demo",
            client_phone="+79991234567",
            step=ConversationStep.ASK_SERVICE,
        )
        response = await agent.run_turn(state, "Хочу стрижку", tools=[])
        assert len(response.tool_calls) > 0
        assert response.tool_calls[0].name == "list_services"
    finally:
        await agent.disconnect()