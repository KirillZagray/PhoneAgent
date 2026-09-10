"""Тест разового голосового уведомления (ConversationState.announcement) —
звонок должен проговорить текст и сразу завершиться, не заводя FSM записи на
услугу. Добавлено для phoneagent-mcp's call_contact(message=...)/call_number(message=...),
которые раньше тихо теряли этот параметр (extra.initial_message никуда не передавался)."""

from __future__ import annotations

import pytest

from phoneagent.connectors.mock import MockBookingConnector
from phoneagent.core.agent import MockLLMAgent
from phoneagent.core.orchestrator import Orchestrator
from phoneagent.core.state_store import InMemoryStateStore
from phoneagent.models.conversation import ConversationState, ConversationStep
from phoneagent.providers.stt.mock import MockSTTProvider
from phoneagent.providers.telephony.mock import MockTelephonyProvider
from phoneagent.providers.tts.mock import MockTTSProvider


def _build_orchestrator() -> Orchestrator:
    return Orchestrator(
        telephony=MockTelephonyProvider(),
        stt=MockSTTProvider(),
        tts=MockTTSProvider(),
        llm=MockLLMAgent(),
        booking=MockBookingConnector(),
        state_store=InMemoryStateStore(),
    )


@pytest.mark.asyncio
async def test_announcement_call_says_text_once_and_ends() -> None:
    orchestrator = _build_orchestrator()
    state = ConversationState(
        call_id="call-announce-1",
        salon_id="demo",
        client_phone="+79991234567",
        announcement="Всё готово!",
    )

    said: list[str] = []

    async def fake_say(s: ConversationState, text: str) -> None:
        said.append(text)

    orchestrator._say = fake_say  # type: ignore[method-assign]

    await orchestrator._dialog_loop(state)

    assert said == ["Всё готово!"]
    assert state.step == ConversationStep.END


@pytest.mark.asyncio
async def test_no_announcement_uses_normal_salon_greeting() -> None:
    """Регрессия: обычный (не-announcement) звонок не должен сломаться —
    _listen() в mock-режиме сразу возвращает "", # значит после приветствия
    и одного неуслышанного захода FSM должен пойти в retry, а не в END."""
    orchestrator = _build_orchestrator()
    state = ConversationState(
        call_id="call-announce-2",
        salon_id="demo",
        client_phone="+79991234567",
    )

    said: list[str] = []

    async def fake_say(s: ConversationState, text: str) -> None:
        said.append(text)
        if len(said) >= orchestrator.settings.max_retries + 2:
            s.step = ConversationStep.END  # обрываем тест, не гоняя реальный retry-цикл целиком

    orchestrator._say = fake_say  # type: ignore[method-assign]

    await orchestrator._dialog_loop(state)

    assert said[0].startswith("Здравствуйте!")
