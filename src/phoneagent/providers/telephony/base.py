"""Базовый интерфейс провайдера телефонии.

Любая реализация (Voximplant, Twilio, Asterisk, Mock) должна имплементировать
этот ABC. Бизнес-логика Orchestrator работает только через этот интерфейс.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from phoneagent.models.call import CallEvent, CallRef, CallStatus


class BaseTelephonyProvider(ABC):
    """Базовый класс для всех провайдеров телефонии.

    Поток аудио:
        provider  ->  InboundAudioStream (STT)        (когда клиент говорит)
        OutboundAudioStream (TTS) -> provider         (когда агент говорит)
    """

    name: str = "base"

    #: True только когда send_audio/send_text реально реализованы и звонок можно
    #: провести целиком. Провайдеры-заготовки (Twilio/Voximplant/Asterisk сейчас)
    #: держат False, чтобы Orchestrator не набирал реальный номер и не ронял звонок
    #: сразу после соединения (см. roadmap в README).
    supports_realtime_audio: bool = False

    @abstractmethod
    async def connect(self) -> None:
        """Инициализация клиента провайдера (HTTP-сессии, авторизация)."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Закрытие клиента."""
        ...

    @abstractmethod
    async def make_call(self, to_phone: str, **kwargs: Any) -> CallRef:
        """Инициирует исходящий звонок на номер.

        Args:
            to_phone: номер клиента в формате E.164 (+79991234567)
            kwargs: доп. параметры (webhook_url, scenario_id и т.п.)

        Returns:
            CallRef с ID звонка у провайдера
        """
        ...

    @abstractmethod
    async def hangup(self, call_id: str) -> None:
        """Завершает активный звонок."""
        ...

    @abstractmethod
    async def get_status(self, call_id: str) -> CallStatus:
        """Возвращает текущий статус звонка."""
        ...

    @abstractmethod
    def events(self) -> AsyncIterator[CallEvent]:
        """Поток событий от провайдера (WebSocket / polling).

        События: started, connected, audio, dtmf, ended, error.

        Должен быть async generator (async def + yield), не async coroutine.
        """
        ...

    @abstractmethod
    async def send_audio(self, call_id: str, audio: AsyncIterator[bytes]) -> None:
        """Отправляет аудио в звонок (TTS-поток)."""
        ...

    @abstractmethod
    async def send_text(self, call_id: str, text: str) -> None:
        """Отправляет текст в звонок (для провайдеров с встроенным TTS)."""
        ...

    async def __aenter__(self) -> BaseTelephonyProvider:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()
