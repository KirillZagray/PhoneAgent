"""Базовый интерфейс провайдера TTS."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class BaseTTSProvider(ABC):
    """Базовая абстракция для синтеза речи.

    Контракт выхода (и synthesize, и synthesize_stream): сырой PCM 16-bit signed
    little-endian, моно, ровно на запрошенном `sample_rate`. Без WAV-заголовка,
    без MP3. Всё, что провайдер отдаёт в другом виде, он конвертирует сам
    (см. utils/audio.py) — телефония получает байты как есть.
    """

    name: str = "base"

    @abstractmethod
    async def connect(self) -> None:
        """Инициализация клиента."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Закрытие клиента."""
        ...

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> bytes:
        """Синтезирует речь и возвращает аудио целиком (bytes)."""
        ...

    @abstractmethod
    def synthesize_stream(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[bytes]:
        """Стриминговый синтез — чанки по мере генерации.

        Должен быть async generator (async def + yield).
        """
        ...

    async def __aenter__(self) -> BaseTTSProvider:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.disconnect()