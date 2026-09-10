"""LLM-агент с tool calling.

Поддерживает Anthropic и OpenAI. Mock-провайдер для тестов.

Tool-calling раунды (LLM просит tool -> мы выполняем -> отдаём результат
обратно LLM -> LLM отвечает текстом) полностью происходят *внутри* одного
`run_turn()`. Наружу (в ConversationState.messages) попадает только
финальный текст пользователя/ассистента — так и Anthropic, и OpenAI получают
на следующий turn корректную историю без "полу-собранных" tool_use блоков.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from phoneagent.config import get_settings
from phoneagent.core.prompts import get_system_prompt
from phoneagent.models.conversation import ConversationState, Role
from phoneagent.utils import get_logger

logger = get_logger(__name__)

# Максимум раундов "LLM просит tool -> получает результат" за один turn.
# Защита от зацикливания, если модель никак не остановится.
MAX_TOOL_ROUNDS = 5

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]


class ToolCall:
    """Tool call от LLM."""

    def __init__(self, name: str, arguments: dict[str, Any], call_id: str = "") -> None:
        self.name = name
        self.arguments = arguments
        self.call_id = call_id


class AgentResponse:
    """Ответ LLM-агента (уже после разрешения всех tool calls этого turn'а)."""

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
        *,
        execute_tool: ToolExecutor | None = None,
    ) -> AgentResponse:
        """Один turn диалога: пользователь говорит → агент отвечает + tool calls.

        Если модель запрашивает tool, реализация вызывает `execute_tool(name, arguments)`
        и продолжает цикл сама — наружу возвращается уже финальный ответ.
        """
        ...

    def _text_history(self, state: ConversationState) -> list[dict[str, str]]:
        """История для API: только законченные user/assistant реплики, последние N.

        Tool-раунды не реплеим — они уже разрешены внутри своего turn'а.
        """
        history = [
            {"role": m.role.value, "content": m.content}
            for m in state.messages
            if m.role in (Role.USER, Role.ASSISTANT)
        ]
        return history[-get_settings().max_history_messages :]

    def _system_prompt(self, state: ConversationState) -> str:
        settings = get_settings()
        return get_system_prompt(
            state.language,
            salon_name=settings.salon_name,
            working_hours=settings.salon_working_hours,
            timezone=settings.salon_timezone,
        )


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
        "description": (
            "Создать запись на услугу. Только после явного подтверждения клиента. "
            "Телефон клиента системе уже известен — передавать не нужно."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "ID услуги"},
                "master_id": {"type": "string", "description": "ID мастера"},
                "date": {"type": "string", "description": "Дата YYYY-MM-DD"},
                "time": {"type": "string", "description": "Время HH:MM"},
                "client_name": {"type": "string", "description": "Имя клиента (опционально)"},
            },
            "required": ["service_id", "master_id", "date", "time"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Перевести звонок на живого администратора: клиент просит человека, "
            "недоволен, или задача вне твоих возможностей. Завершает диалог с ассистентом."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Краткая причина эскалации"},
            },
            "required": ["reason"],
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
        *,
        execute_tool: ToolExecutor | None = None,
    ) -> AgentResponse:
        text_lower = user_message.lower()

        # Эскалация — единственный tool, который мок реально исполняет (через execute_tool),
        # чтобы путь ESCALATE в Orchestrator был проверяем без реального LLM.
        if any(w in text_lower for w in ("администратор", "оператор", "человек")):
            call = ToolCall(name="escalate_to_human", arguments={"reason": "client asked"}, call_id="esc_1")
            if execute_tool is not None:
                await execute_tool(call.name, call.arguments)
            return AgentResponse(
                text="Переключаю вас на администратора, оставайтесь на линии.",
                tool_calls=[call],
                is_final=True,
            )

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

        self._client = AsyncAnthropic(
            api_key=self.settings.anthropic_api_key,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=1,
        )
        logger.info("anthropic_connected", model=self.settings.anthropic_model)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()

    async def run_turn(
        self,
        state: ConversationState,
        user_message: str,
        tools: list[dict[str, Any]],
        *,
        execute_tool: ToolExecutor | None = None,
    ) -> AgentResponse:
        if self._client is None:
            msg = "Anthropic client not connected"
            raise RuntimeError(msg)

        anthropic_tools = [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in tools
        ]

        messages: list[dict[str, Any]] = self._text_history(state)
        if user_message:
            messages.append({"role": "user", "content": user_message})

        executed_tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        stop_reason = ""

        for _ in range(MAX_TOOL_ROUNDS):
            response = await self._client.messages.create(
                model=self.settings.anthropic_model,
                max_tokens=512,
                system=self._system_prompt(state),
                tools=anthropic_tools,
                messages=messages,
            )
            stop_reason = response.stop_reason
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_parts.extend(b.text for b in response.content if b.type == "text")

            if not tool_uses:
                break

            # Ассистентский ход с tool_use блоками уходит в историю как есть.
            messages.append({"role": "assistant", "content": response.content})

            tool_result_blocks: list[dict[str, Any]] = []
            for block in tool_uses:
                call = ToolCall(name=block.name, arguments=block.input, call_id=block.id)
                executed_tool_calls.append(call)
                if execute_tool is None:
                    result: Any = {"error": "no tool executor configured"}
                else:
                    result = await execute_tool(call.name, call.arguments)
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str, ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": tool_result_blocks})
        else:
            logger.warning("anthropic_tool_round_limit_reached", call_id=state.call_id)

        return AgentResponse(
            text=" ".join(p for p in text_parts if p),
            tool_calls=executed_tool_calls,
            is_final=stop_reason == "end_turn",
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

        self._client = AsyncOpenAI(
            api_key=self.settings.openai_api_key,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=1,
        )
        logger.info("openai_connected", model=self.settings.openai_model)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()

    async def run_turn(
        self,
        state: ConversationState,
        user_message: str,
        tools: list[dict[str, Any]],
        *,
        execute_tool: ToolExecutor | None = None,
    ) -> AgentResponse:
        if self._client is None:
            msg = "OpenAI client not connected"
            raise RuntimeError(msg)

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

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(state)},
            *self._text_history(state),
        ]
        if user_message:
            messages.append({"role": "user", "content": user_message})

        executed_tool_calls: list[ToolCall] = []
        text = ""

        for _ in range(MAX_TOOL_ROUNDS):
            response = await self._client.chat.completions.create(
                model=self.settings.openai_model,
                messages=messages,
                tools=openai_tools,
                # Не max_tokens — новые модели (gpt-5.x и т.п.) отклоняют этот
                # параметр как unsupported_parameter, ждут max_completion_tokens.
                max_completion_tokens=512,
            )
            msg_response = response.choices[0].message
            text = msg_response.content or ""

            if not msg_response.tool_calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": msg_response.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg_response.tool_calls
                    ],
                }
            )
            for tc in msg_response.tool_calls:
                arguments = json.loads(tc.function.arguments)
                call = ToolCall(name=tc.function.name, arguments=arguments, call_id=tc.id)
                executed_tool_calls.append(call)
                if execute_tool is None:
                    result: Any = {"error": "no tool executor configured"}
                else:
                    result = await execute_tool(call.name, call.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str, ensure_ascii=False),
                    }
                )
        else:
            logger.warning("openai_tool_round_limit_reached", call_id=state.call_id)

        return AgentResponse(text=text, tool_calls=executed_tool_calls, is_final=True)


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
    "TOOL_DESCRIPTIONS",
    "AgentResponse",
    "BaseLLMAgent",
    "ToolExecutor",
    "build_llm_agent",
]
