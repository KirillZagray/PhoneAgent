"""Базовый интерфейс провайдера STT."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class BaseSTTProvider(ABC):
    """Базовая абстракция для провайдера распознавания речи.

    Принимает аудио-поток (μ-law или PCM от провайдера телефонии),
    выдаёт распознанный текст.
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
    async def transcribe(
        self,
        audio: bytes | AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> str:
        """Распознаёт речь из аудио (или потока чанков).

        Args:
            audio: сырое аудио или async iterator по чанкам
            language: BCP-47 код языка
            sample_rate: частота дискретизации в Гц

        Returns:
            Распознанный текст
        """
        ...

    @abstractmethod
    def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        *,
        language: str = "ru",
        sample_rate: int = 8000,
    ) -> AsyncIterator[str]:
        """Потоковое распознавание — выдаёт промежуточные результаты по мере поступления аудио.

        Возвращает async iterator по фрагментам текста (partial / final).

        Должен быть async generator (async def + yield), не async coroutine.
        """
        ...

    async def __aenter__(self) -> BaseSTTProvider:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.disconnect()