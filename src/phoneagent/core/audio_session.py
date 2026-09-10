"""Общая точка соединения между аудио-мостами и Orchestrator/телефонией — Phase B.

Мосты (`api/media_ws.py` для Voximplant, `api/audiosocket_server.py` для
Asterisk) знают только свой wire-протокол и общий контракт: сырой PCM16
little-endian mono на `settings.sample_rate`. Всё, что выше контракта (VAD,
STT, LLM, TTS) не знает, какой это провайдер — работает только через
AudioSession, поэтому Phase B логика в Orchestrator одна на всех провайдеров.

Поток:
    Мост (входящее аудио)  -> session.push_incoming() -> Orchestrator._listen()
    Orchestrator._say()    -> telephony.send_audio()  -> session.push_outgoing()
        -> Мост вычитывает через paced_frames() и шлёт в трубку с реальным
           таймингом (иначе телефония получит весь ответ одним всплеском
           вместо живой речи — RTP/AudioSocket сами не темпируют исходящее).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from phoneagent.utils import get_logger

logger = get_logger(__name__)


class AudioSession:
    """Пара очередей сырых PCM16-чанков для одного звонка."""

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.outgoing: asyncio.Queue[bytes | None] = asyncio.Queue()
        # Сигнал "Orchestrator закончил разговор, физически повесь трубку".
        # Нужен для входящих звонков: там, в отличие от исходящих (Voximplant
        # StartScenarios), у telephony-провайдера нет прямого REST-способа
        # завершить конкретный звонок — единственный канал наружу к
        # VoxEngine-сценарию (или Asterisk-каналу) — сам мост, и он узнаёт о
        # необходимости завершения через этот Event, а не через провайдера
        # напрямую.
        self.hangup_requested = asyncio.Event()
        # Сколько чанков положено в outgoing, но ещё не забрано мостом —
        # см. wait_drained(). Считаем по чанкам, не по байтам: paced_frames
        # копит их во фрейм-буфер и может годами не докопить до полного
        # кадра, если мерить точность на уровне байт — а чанк "забран" в
        # тот момент, когда мост его вычитал из очереди, вне зависимости от
        # того, как он его дальше нарезает.
        self._pending_out = 0
        self._drained = asyncio.Event()
        self._drained.set()  # изначально нечего ждать

    def push_incoming(self, chunk: bytes) -> None:
        self.incoming.put_nowait(chunk)

    def push_outgoing(self, chunk: bytes) -> None:
        self._pending_out += 1
        self._drained.clear()
        self.outgoing.put_nowait(chunk)

    def mark_outgoing_taken(self) -> None:
        """Вызывает транспорт (paced_frames) сразу после того, как забрал
        чанк из `outgoing` — сигнал "этот кусок пошёл на реальную отправку".
        Не приватный (без `_`) — вызывается из соседнего модуля-моста.
        """
        self._pending_out = max(0, self._pending_out - 1)
        if self._pending_out == 0:
            self._drained.set()

    async def wait_drained(self) -> None:
        """Ждёт, пока все чанки, положенные в `outgoing` к этому моменту, не
        будут вычитаны мостом. С точностью до ~одного кадра (обычно 20ms) —
        столько может лежать недоотправленным в буфере темпирования
        paced_frames, это не критично для целей echo-guard в Orchestrator.
        """
        await self._drained.wait()

    def request_hangup(self) -> None:
        self.hangup_requested.set()

    def close(self) -> None:
        """Будит всех читателей обеих очередей sentinel-ом None — иначе
        get() на пустой очереди висит вечно после того, как мост отключился.
        """
        self.incoming.put_nowait(None)
        self.outgoing.put_nowait(None)


_sessions: dict[str, AudioSession] = {}


def create_session(call_id: str) -> AudioSession:
    session = AudioSession(call_id)
    _sessions[call_id] = session
    return session


def get_session(call_id: str) -> AudioSession | None:
    return _sessions.get(call_id)


def remove_session(call_id: str) -> None:
    session = _sessions.pop(call_id, None)
    if session is not None:
        session.close()


async def wait_for_session(
    call_id: str, *, timeout: float = 10.0, poll_interval: float = 0.1
) -> AudioSession | None:
    """Ждёт, пока мост реально подключится и создаст сессию.

    Нужен из-за гонки: Orchestrator запускает диалог (и первую же say())
    сразу после make_call(), а сам медиа-канал (VoxEngine WS / AudioSocket)
    провайдер открывает только после того, как звонок физически соединился
    — на секунду-другую позже. Поллинг, не asyncio.Event: проще, а
    десятки-сотни мс на старте звонка роли не играют.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        session = _sessions.get(call_id)
        if session is not None:
            return session
        await asyncio.sleep(poll_interval)
    return _sessions.get(call_id)


async def paced_frames(
    session: AudioSession, *, frame_ms: int, sample_rate: int
) -> AsyncIterator[bytes]:
    """Читает `session.outgoing` и отдаёт кадры фиксированного размера с
    реальным таймингом (по `frame_ms` между кадрами) — то, что телефония
    ожидает от живого голосового потока.

    TTS отдаёт чанки произвольного размера (не обязательно кратные кадру),
    поэтому копим в буфере и режем ровно по frame_bytes. Последний неполный
    кадр при закрытии сессии — добиваем тишиной, а не отбрасываем (короткий
    хвост фразы всё равно должен дойти до собеседника).
    """
    frame_bytes = int(sample_rate * frame_ms / 1000) * 2  # PCM16 mono = 2 байта/сэмпл
    frame_seconds = frame_ms / 1000
    buffer = bytearray()
    loop = asyncio.get_event_loop()
    next_send_at: float | None = None

    while True:
        chunk = await session.outgoing.get()
        if chunk is None:
            break
        session.mark_outgoing_taken()
        buffer.extend(chunk)
        while len(buffer) >= frame_bytes:
            frame = bytes(buffer[:frame_bytes])
            del buffer[:frame_bytes]
            now = loop.time()
            if next_send_at is not None and next_send_at > now:
                await asyncio.sleep(next_send_at - now)
            next_send_at = max(now, next_send_at or now) + frame_seconds
            yield frame

    if buffer:
        yield bytes(buffer) + b"\x00" * (frame_bytes - len(buffer))


__all__ = [
    "AudioSession",
    "create_session",
    "get_session",
    "paced_frames",
    "remove_session",
    "wait_for_session",
]
