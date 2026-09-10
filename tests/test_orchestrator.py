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

@pytest.mark.asyncio
async def test_handle_inbound_call_creates_state_and_schedules_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Входящий звонок: в отличие от handle_callback, тут нет make_call() —
    состояние создаётся сразу, обработка звонка запускается в фоне."""
    from phoneagent.config import get_settings
    from phoneagent.connectors.mock import MockBookingConnector
    from phoneagent.core.orchestrator import Orchestrator
    from phoneagent.core.state_store import InMemoryStateStore
    from phoneagent.models.conversation import ConversationStep
    from phoneagent.providers.stt.mock import MockSTTProvider
    from phoneagent.providers.telephony.mock import MockTelephonyProvider
    from phoneagent.providers.tts.mock import MockTTSProvider

    for key in ("TELEPHONY_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER", "BOOKING_CONNECTOR"):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("STATE_STORE", "memory")
    get_settings.cache_clear()

    orchestrator = Orchestrator(
        telephony=MockTelephonyProvider(),
        stt=MockSTTProvider(),
        tts=MockTTSProvider(),
        llm=MockLLMAgent(),
        booking=MockBookingConnector(),
        state_store=InMemoryStateStore(),
    )

    await orchestrator.handle_inbound_call("inbound-1", "demo", "+79990001122")

    state = await orchestrator.state_store.get("inbound-1")
    assert state is not None
    assert state.client_phone == "+79990001122"
    assert state.step == ConversationStep.GREETING
    assert len(orchestrator._background_tasks) == 1

    # Не ждём полного диалога (mock STT уходит в несколько retry-циклов по
    # 0.5s) — достаточно знать, что обработка реально запущена в фоне.
    for task in list(orchestrator._background_tasks):
        task.cancel()
    get_settings.cache_clear()
