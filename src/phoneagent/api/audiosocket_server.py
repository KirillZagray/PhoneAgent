"""Аудио-мост Asterisk AudioSocket <-> PhoneAgent.

Phase B: реальная передача аудио. Мост не знает про STT/LLM/TTS — он просто
переливает байты между Asterisk и общей `AudioSession` для этого call_id
(см. core/audio_session.py); вся логика диалога — в Orchestrator. Тот же
принцип, что и у моста Voximplant (`api/media_ws.py`), другой только wire-формат.

AudioSocket — двоичный протокол поверх голого TCP (модуль Asterisk
`app_audiosocket`):

    [1 байт type][2 байта length, big-endian][length байт payload]

    type=0x01 UUID   — первый фрейм от Asterisk, 16 байт, id звонка
                        (тот же call_id, что передан аргументом в AudioSocket()
                        в dialplan — см. asterisk_conf/extensions.conf)
    type=0x10 AUDIO  — raw PCM16 8kHz mono, обе стороны
    type=0x00 HANGUP — Asterisk сообщает о завершении звонка
    type=0xff ERROR  — ошибка на стороне Asterisk

Никакого HTTP-заголовка с секретом тут нет (это не HTTP) — авторизация не по
токену, а по сетевой изоляции: сервер слушает только в docker-compose сети,
снаружи порт не публикуется, достучаться может только контейнер Asterisk.
"""

from __future__ import annotations

import asyncio
import uuid

from phoneagent.config import get_settings
from phoneagent.core.audio_session import AudioSession, create_session, paced_frames, remove_session
from phoneagent.utils import get_logger

logger = get_logger(__name__)

KIND_HANGUP = 0x00
KIND_UUID = 0x01
KIND_DTMF = 0x03
KIND_AUDIO = 0x10
KIND_ERROR = 0xFF

_HEADER_SIZE = 3  # 1 байт type + 2 байта length


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
    """Читает один AudioSocket-фрейм. None — соединение закрыто (EOF)."""
    header = await reader.readexactly(_HEADER_SIZE)
    kind = header[0]
    length = int.from_bytes(header[1:3], byteorder="big")
    payload = await reader.readexactly(length) if length else b""
    return kind, payload


def _write_frame(writer: asyncio.StreamWriter, kind: int, payload: bytes) -> None:
    writer.write(bytes([kind]) + len(payload).to_bytes(2, byteorder="big") + payload)


async def _send_outgoing_audio(writer: asyncio.StreamWriter, session: AudioSession) -> None:
    """Вычитывает AudioSession.outgoing (TTS-ответы Orchestrator) и шлёт в
    Asterisk AudioSocket-кадрами фиксированного размера с реальным
    таймингом — см. paced_frames про то, зачем это нужно (AudioSocket сам
    не темпирует исходящее, просто ретранслирует что дали).
    """
    settings = get_settings()
    try:
        async for frame in paced_frames(
            session, frame_ms=settings.audio_frame_ms, sample_rate=settings.sample_rate
        ):
            _write_frame(writer, KIND_AUDIO, frame)
            await writer.drain()
    except asyncio.CancelledError:
        raise
    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception:
        logger.exception("audiosocket_sender_crashed", call_id=session.call_id)


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    call_id = "unknown"
    sender_task: asyncio.Task[None] | None = None
    try:
        first = await _read_frame(reader)
        if first is None:
            return
        kind, payload = first
        if kind != KIND_UUID or len(payload) != 16:
            logger.warning("audiosocket_bad_handshake", peer=peer, kind=kind)
            return
        call_id = str(uuid.UUID(bytes=payload))
        logger.info("audiosocket_connected", call_id=call_id, peer=peer)

        session = create_session(call_id)
        sender_task = asyncio.create_task(_send_outgoing_audio(writer, session))

        while True:
            frame = await _read_frame(reader)
            if frame is None:
                break
            kind, payload = frame
            if kind == KIND_AUDIO:
                session.push_incoming(payload)
            elif kind == KIND_HANGUP:
                logger.info("audiosocket_hangup_frame", call_id=call_id)
                break
            elif kind == KIND_DTMF:
                logger.debug("audiosocket_dtmf", call_id=call_id, digit=payload.decode(errors="replace"))
            elif kind == KIND_ERROR:
                logger.warning("audiosocket_error_frame", call_id=call_id)
                break
            else:
                logger.debug("audiosocket_unknown_frame", call_id=call_id, kind=kind)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        logger.info("audiosocket_disconnected", call_id=call_id)
    finally:
        if sender_task is not None:
            sender_task.cancel()
        remove_session(call_id)
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass


async def start_audiosocket_server(host: str, port: int) -> asyncio.base_events.Server:
    """Запускает TCP-сервер AudioSocket-моста. Вызывается из main.lifespan
    только когда TELEPHONY_PROVIDER=asterisk (см. main.py)."""
    server = await asyncio.start_server(_handle_connection, host=host, port=port)
    logger.info("audiosocket_server_started", host=host, port=port)
    return server


__all__ = ["start_audiosocket_server"]
