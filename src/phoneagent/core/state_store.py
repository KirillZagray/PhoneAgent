"""Хранилище состояний диалогов (Redis или in-memory)."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod

import redis.asyncio as aioredis

from phoneagent.config import get_settings
from phoneagent.models.conversation import ConversationState
from phoneagent.utils import get_logger, redact_url

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

    @abstractmethod
    async def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        """Атомарно ставит замок на ttl. False — замок уже стоит (дедуп повторных звонков)."""
        ...


class InMemoryStateStore(BaseStateStore):
    """In-memory хранилище для dev/тестов. TTL честный, но без фонового GC —
    подчистка на каждой записи (ponytail: O(n), для dev ок)."""

    def __init__(self) -> None:
        self._states: dict[str, tuple[ConversationState, float]] = {}
        self._locks: dict[str, float] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        self._states = {k: v for k, v in self._states.items() if v[1] > now}
        self._locks = {k: exp for k, exp in self._locks.items() if exp > now}

    async def get(self, call_id: str) -> ConversationState | None:
        entry = self._states.get(call_id)
        if entry is None or entry[1] <= time.monotonic():
            self._states.pop(call_id, None)
            return None
        return entry[0]

    async def set(self, state: ConversationState, ttl_seconds: int = 3600) -> None:
        self._prune()
        self._states[state.call_id] = (state, time.monotonic() + ttl_seconds)

    async def delete(self, call_id: str) -> None:
        self._states.pop(call_id, None)

    async def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        self._prune()
        if key in self._locks:
            return False
        self._locks[key] = time.monotonic() + ttl_seconds
        return True


class RedisStateStore(BaseStateStore):
    """Redis-хранилище состояний диалогов."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.settings = get_settings()
        self._url = redis_url or str(self.settings.redis_url)
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(self._url, decode_responses=True)
        await self._client.ping()
        logger.info("redis_state_store_connected", url=redact_url(self._url))

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    def _client_or_raise(self) -> aioredis.Redis:
        if self._client is None:
            msg = "Redis not connected"
            raise RuntimeError(msg)
        return self._client

    @staticmethod
    def _key(call_id: str) -> str:
        return f"phoneagent:state:{call_id}"

    async def get(self, call_id: str) -> ConversationState | None:
        data = await self._client_or_raise().get(self._key(call_id))
        if data is None:
            return None
        # ISO-строки дат pydantic парсит сам
        return ConversationState.model_validate_json(data)

    async def set(self, state: ConversationState, ttl_seconds: int = 3600) -> None:
        payload = json.dumps(state.model_dump(mode="json"), ensure_ascii=False)
        await self._client_or_raise().set(self._key(state.call_id), payload, ex=ttl_seconds)

    async def delete(self, call_id: str) -> None:
        await self._client_or_raise().delete(self._key(call_id))

    async def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        ok = await self._client_or_raise().set(f"phoneagent:lock:{key}", "1", nx=True, ex=ttl_seconds)
        return bool(ok)


def build_state_store() -> BaseStateStore:
    """Создаёт хранилище по настройкам (для прод — Redis, для dev/тестов — memory)."""
    settings = get_settings()
    if settings.state_store == "memory":
        return InMemoryStateStore()
    return RedisStateStore()


__all__ = [
    "BaseStateStore",
    "InMemoryStateStore",
    "RedisStateStore",
    "build_state_store",
]
