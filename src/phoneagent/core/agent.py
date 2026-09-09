"""LLM-агент с tool calling.

Поддерживает Anthropic и OpenAI. Mock-провайдер для тестов.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from phoneagent.config import get_settings
from phoneagent.core.prompts import get_system_prompt
from phoneagent.models.booking import Master, Service, Slot
from phoneagent.models.conversation import ConversationState, Message, Role
from phoneagent.utils import get_logger

logger = get_logger(__name__)


class ToolCall:
    """Tool call от LLM."""

    def __init__(self, name: str, arguments: dict[str, Any], call_id: str = "") -> None:
        self.name = name
        self.arguments = arguments
        self.call_id = call_id


class AgentResponse:
    """Ответ LLM-агента."""

    def __init__(
        self,
        text: str,
        tool_calls: list[ToolCall] | None = None,
        *,
        is_final: bool = False,
    ) -> None:
        self.text = text
        self.tool_calls = tool_calls or []
        self.is_final = is_final


class BaseLLMAgent(ABC):
    """Базовый интерфейс LLM-агента."""

    name: str = "base"

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    async def run_turn(
        self,
        state: ConversationState,
        user_message: str,
        tools: list[dict[str, Any]],
    ) -> AgentResponse:
        """Один turn диалога: пользователь говорит → агент отвечает + tool calls."""
        ...


# ── Tool descriptions (передаются в LLM) ──────────────────────

TOOL_DESCRIPTIONS = [
    {
        "name": "list_services",
        "description": "Получить список доступных услуг салона (стрижка, маникюр и т.д.)",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_masters",
        "description": "Получить список мастеров. Можно фильтровать по услуге.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "ID услуги (опционально)"},
            },
        },
    },
    {
        "name": "list_slots",
        "description": "Получить свободные слоты мастера на указанную дату (формат YYYY-MM-DD).",
        "input_schema": {
            "type": "object",
            "properties": {
                "master_id": {"type": "string", "description": "ID мастера"},
                "date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"},
                "service_id": {"type": "string", "description": "ID услуги (опционально)"},
            },
            "required": ["master_id", "date"],
        },
    },
    {
        "name": "create_booking",
        "description": "Создать запись на услугу. Только после подтверждения клиента.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "ID услуги"},
                "master_id": {"type": "string", "description": "ID мастера"},
                "date": {"type": "string", "description": "Дата YYYY-MM-DD"},
                "time": {"type": "string", "description": "Время HH:MM"},
                "client_phone": {"type": "string", "description": "Телефон клиента"},
                "client_name": {"type": "string", "description": "Имя клиента (опционально)"},
            },
            "required": ["service_id", "master_id", "date", "time", "client_phone"],
        },
    },
]


# ── Mock agent ────────────────────────────────────────


class MockLLMAgent(BaseLLMAgent):
    """Заглушка LLM для разработки и тестов.

    Использует простую FSM на ключевых словах — НЕ настоящий LLM.
    Только для smoke-тестов без вызова API.
    """

    name = "mock"

    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def run_turn(
        self,
        state: ConversationState,
        user_message: str,
        tools: list[dict[str, Any]],
    ) -> AgentResponse:
        text_lower = user_message.lower()

        # Грубая эвристика для теста FSM
        if state.step.value == "greeting":
            state.step = state.step.__class__.ASK_SERVICE
            return AgentResponse(
                text="Здравствуйте! Я AI-ассистент салона красоты. Какую услугу вы хотите записать?"
            )

        if state.step.value == "ask_service":
            # Примитивный матчинг услуги
            if "стриж" in text_lower or "подстрич" in text_lower:
                return AgentResponse(
                    text="Хорошо, стрижка. На какую дату записать?",
                    tool_calls=[
                        ToolCall(
                            name="list_services",
                            arguments={},
                            call_id="call_1",
                        )
                    ],
                )
            return AgentResponse(text="Подскажите, какая услуга вас интересует?")

        if state.step.value == "ask_date":
            return AgentResponse(
                text="Спасибо. К какому мастеру записать?",
                tool_calls=[ToolCall(name="list_masters", arguments={}, call_id="call_2")],
            )

        return AgentResponse(text="Понял. Продолжим?")


# ── Anthropic Claude ───────────────────────────────────


class AnthropicLLMAgent(BaseLLMAgent):
    """LLM-агент на базе Anthropic Claude."""

    name = "anthropic"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Any = None

    async def connect(self) -> None:
        if not self.settings.anthropic_api_key:
            msg = "ANTHROPIC_API_KEY not set"
            raise RuntimeError(msg)
        from anthropic import AsyncAnthropic
        self._client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        logger.info("anthropic_connected", model=self.settings.anthropic_model)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()

    async def run_turn(
        self,
        state: ConversationState,
        user_message: str,
        tools: list[dict[str, Any]],
    ) -> AgentResponse:
        if self._client is None:
            msg = "Anthropic client not connected"
            raise RuntimeError(msg)

        # Формируем messages из state.messages + user_message
        messages = [{"role": m.role.value if m.role != Role.TOOL else "user", "content": m.content}
                    for m in state.messages]
        messages.append({"role": "user", "content": user_message})

        # Tools: конвертируем в формат Anthropic
        anthropic_tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in tools
        ]

        response = await self._client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=512,
            system=get_system_prompt(state.language),
            tools=anthropic_tools,
            messages=messages,
        )

        # Извлекаем текст и tool calls
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        name=block.name,
                        arguments=block.input,
                        call_id=block.id,
                    )
                )

        return AgentResponse(
            text=" ".join(text_parts),
            tool_calls=tool_calls,
            is_final=response.stop_reason == "end_turn",
        )


# ── OpenAI ─────────────────────────────────────────────


class OpenAILLMAgent(BaseLLMAgent):
    """LLM-агент на базе OpenAI."""

    name = "openai"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Any = None

    async def connect(self) -> None:
        if not self.settings.openai_api_key:
            msg = "OPENAI_API_KEY not set"
            raise RuntimeError(msg)
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        logger.info("openai_connected", model=self.settings.openai_model)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()

    async def run_turn(
        self,
        state: ConversationState,
        user_message: str,
        tools: list[dict[str, Any]],
    ) -> AgentResponse:
        if self._client is None:
            msg = "OpenAI client not connected"
            raise RuntimeError(msg)

        # OpenAI формат tools
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

        messages = [
            {"role": "system", "content": get_system_prompt(state.language)},
            *[{"role": m.role.value if m.role != Role.TOOL else "tool", "content": m.content}
              for m in state.messages],
            {"role": "user", "content": user_message},
        ]

        response = await self._client.chat.completions.create(
            model=self.settings.openai_model,
            messages=messages,
            tools=openai_tools,
            max_tokens=512,
        )

        msg_response = response.choices[0].message
        text = msg_response.content or ""
        tool_calls: list[ToolCall] = []
        if msg_response.tool_calls:
            for tc in msg_response.tool_calls:
                tool_calls.append(
                    ToolCall(
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                        call_id=tc.id,
                    )
                )
        return AgentResponse(text=text, tool_calls=tool_calls)


# ── Factory ─────────────────────────────────────────────


def build_llm_agent() -> BaseLLMAgent:
    """Создаёт LLM-агента по настройкам."""
    settings = get_settings()
    provider = settings.llm_provider
    logger.info("building_llm_agent", provider=provider.value)

    if provider.value == "mock":
        return MockLLMAgent()

    if provider.value == "anthropic":
        return AnthropicLLMAgent()

    if provider.value == "openai":
        return OpenAILLMAgent()

    msg = f"Unknown LLM provider: {provider}"
    raise ValueError(msg)


__all__ = [
    "AgentResponse",
    "BaseLLMAgent",
    "TOOL_DESCRIPTIONS",
    "build_llm_agent",
]