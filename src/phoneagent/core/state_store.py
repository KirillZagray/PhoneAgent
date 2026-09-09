"""Хранилище состояний диалогов (Redis или in-memory)."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis

from phoneagent.config import get_settings
from phoneagent.models.conversation import ConversationState
from phoneagent.utils import get_logger

logger = get_logger(__name__)


class BaseStateStore(ABC):
    """Базовый интерфейс хранилища."""

    @abstractmethod
    async def get(self, call_id: str) -> ConversationState | None:
        ...

    @abstractmethod
    async def set(self, state: ConversationState, ttl_seconds: int = 3600) -> None:
        ...

    @abstractmethod
    async def delete(self, call_id: str) -> None:
        ...


class InMemoryStateStore(BaseStateStore):
    """Простое in-memory хранилище для dev."""

    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}

    async def get(self, call_id: str) -> ConversationState | None:
        return self._states.get(call_id)

    async def set(self, state: ConversationState, ttl_seconds: int = 3600) -> None:
        self._states[state.call_id] = state

    async def delete(self, call_id: str) -> None:
        self._states.pop(call_id, None)


class RedisStateStore(BaseStateStore):
    """Redis-хранилище состояний диалогов."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.settings = get_settings()
        url = redis_url or str(self.settings.redis_url)
        self._client: aioredis.Redis | None = None
        self._url = url

    async def connect(self) -> None:
        self._client = aioredis.from_url(self._url, decode_responses=True)
        await self._client.ping()
        logger.info("redis_state_store_connected", url=self._url)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    def _key(self, call_id: str) -> str:
        return f"phoneagent:state:{call_id}"

    async def get(self, call_id: str) -> ConversationState | None:
        if self._client is None:
            msg = "Redis not connected"
            raise RuntimeError(msg)
        data = await self._client.get(self._key(call_id))
        if data is None:
            return None
        raw: dict[str, Any] = json.loads(data)
        # Конвертируем ISO-форматы в datetime
        if "created_at" in raw and isinstance(raw["created_at"], str):
            raw["created_at"] = datetime.fromisoformat(raw["created_at"])
        if "updated_at" in raw and isinstance(raw["updated_at"], str):
            raw["updated_at"] = datetime.fromisoformat(raw["updated_at"])
        return ConversationState.model_validate(raw)

    async def set(self, state: ConversationState, ttl_seconds: int = 3600) -> None:
        if self._client is None:
            msg = "Redis not connected"
            raise RuntimeError(msg)
        data = state.model_dump(mode="json")
        await self._client.set(self._key(state.call_id), json.dumps(data), ex=ttl_seconds)

    async def delete(self, call_id: str) -> None:
        if self._client is None:
            msg = "Redis not connected"
            raise RuntimeError(msg)
        await self._client.delete(self._key(call_id))


def build_state_store() -> BaseStateStore:
    """Создаёт хранилище по настройкам (для прод — Redis)."""
    settings = get_settings()
    if str(settings.redis_url).startswith("redis://"):
        return RedisStateStore()
    return InMemoryStateStore()


__all__ = [
    "BaseStateStore",
    "InMemoryStateStore",
    "RedisStateStore",
    "build_state_store",
]