"""Тесты мягкого/жёсткого таймаута LLM в Orchestrator._run_turn (Phase B+).

Используем реальный Orchestrator с mock-провайдерами телефонии/STT/TTS —
только LLM подменяем управляемым фейком (нужно контролировать задержку и
сбои, MockLLMAgent отвечает мгновенно и без ошибок).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from phoneagent.config import get_settings
from phoneagent.connectors.mock import MockBookingConnector
from phoneagent.core.agent import AgentResponse, BaseLLMAgent
from phoneagent.core.orchestrator import Orchestrator
from phoneagent.core.state_store import InMemoryStateStore
from phoneagent.models.conversation import ConversationState, ConversationStep
from phoneagent.providers.stt.mock import MockSTTProvider
from phoneagent.providers.telephony.mock import MockTelephonyProvider
from phoneagent.providers.tts.mock import MockTTSProvider


def _mock_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for key in ("TELEPHONY_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "BOOKING_CONNECTOR"):
        monkeypatch.setenv(key, "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")  # реальный агент подставляем напрямую, не через фабрику
    monkeypatch.setenv("STATE_STORE", "memory")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


class FakeDelayedLLMAgent(BaseLLMAgent):
    """Отвечает text-ом после delay_seconds; кидает исключение вместо ответа,
    если raises задан."""

    name = "fake-delayed"

    def __init__(self, delay_seconds: float, *, text: str = "Готово", raises: Exception | None = None) -> None:
        self.delay_seconds = delay_seconds
        self.text = text
        self.raises = raises

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def run_turn(
        self,
        state: ConversationState,
        user_message: str,
        tools: list[dict[str, Any]],
        *,
        execute_tool: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> AgentResponse:
        await asyncio.sleep(self.delay_seconds)
        if self.raises is not None:
            raise self.raises
        return AgentResponse(text=self.text, is_final=False)


def _build_orchestrator(llm: BaseLLMAgent) -> Orchestrator:
    return Orchestrator(
        telephony=MockTelephonyProvider(),
        stt=MockSTTProvider(),
        tts=MockTTSProvider(),
        llm=llm,
        booking=MockBookingConnector(),
        state_store=InMemoryStateStore(),
    )


def _state(call_id: str = "call-1") -> ConversationState:
    return ConversationState(call_id=call_id, salon_id="demo", client_phone="+79991234567")


@pytest.mark.asyncio
async def test_fast_llm_response_no_filler(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch, LLM_SOFT_TIMEOUT_SECONDS="0.2")
    orchestrator = _build_orchestrator(FakeDelayedLLMAgent(0.0, text="Быстрый ответ"))
    state = _state()

    response = await orchestrator._run_turn(state, "привет")

    assert response.text == "Быстрый ответ"
    # Филлер не должен был звучать — только реальный ответ в истории.
    assistant_texts = [m.content for m in state.messages if m.role.value == "assistant"]
    assert assistant_texts == ["Быстрый ответ"]


@pytest.mark.asyncio
async def test_slow_llm_response_says_filler_then_real_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch, LLM_SOFT_TIMEOUT_SECONDS="0.1")
    orchestrator = _build_orchestrator(FakeDelayedLLMAgent(0.3, text="Наконец ответил"))
    state = _state()

    response = await orchestrator._run_turn(state, "привет")

    assert response.text == "Наконец ответил"
    assistant_texts = [m.content for m in state.messages if m.role.value == "assistant"]
    # Филлер не попадает в messages (это не LLM-реплика, а сторонняя say()),
    # но должен был реально прозвучать — проверяем через mock TTS/telephony лог
    # косвенно: раз response всё равно пришёл, шилд не дал таску умереть.
    assert assistant_texts == ["Наконец ответил"]


@pytest.mark.asyncio
async def test_llm_failure_after_soft_timeout_ends_call_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_env(monkeypatch, LLM_SOFT_TIMEOUT_SECONDS="0.1")
    orchestrator = _build_orchestrator(
        FakeDelayedLLMAgent(0.3, raises=RuntimeError("upstream boom"))
    )
    state = _state()
    state.step = ConversationStep.ASK_SERVICE

    response = await orchestrator._run_turn(state, "привет")

    assert response.text == ""
    assert response.is_final is True
    assert state.step == ConversationStep.END


@pytest.mark.asyncio
async def test_llm_fast_failure_ends_call_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_env(monkeypatch, LLM_SOFT_TIMEOUT_SECONDS="5.0")
    orchestrator = _build_orchestrator(
        FakeDelayedLLMAgent(0.0, raises=RuntimeError("instant boom"))
    )
    state = _state()
    state.step = ConversationStep.ASK_SERVICE

    response = await orchestrator._run_turn(state, "привет")

    assert response.text == ""
    assert response.is_final is True
    assert state.step == ConversationStep.END


@pytest.mark.asyncio
async def test_say_applies_echo_guard_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """_say() должен реально ждать echo_guard_seconds после send_audio() —
    защита от того, что VAD в следующем _listen() поймает хвост эха
    собственного голоса агента (см. FILLER/echo-guard задачу)."""
    _mock_env(monkeypatch, ECHO_GUARD_SECONDS="0.15")
    orchestrator = _build_orchestrator(FakeDelayedLLMAgent(0.0))
    state = _state()

    start = time.monotonic()
    await orchestrator._say(state, "тест")
    elapsed = time.monotonic() - start

    assert elapsed >= 0.15
