"""Тесты core/audio_session.py — реестр сессий и пейсинг исходящего аудио."""

from __future__ import annotations

import asyncio
import time

import pytest

from phoneagent.core.audio_session import (
    create_session,
    get_session,
    paced_frames,
    remove_session,
    wait_for_session,
)


def test_new_session_starts_drained() -> None:
    session = create_session("call-drained-0")
    try:
        assert session._drained.is_set()
    finally:
        remove_session("call-drained-0")


@pytest.mark.asyncio
async def test_wait_drained_blocks_until_all_pushed_chunks_taken() -> None:
    session = create_session("call-drained-1")
    try:
        session.push_outgoing(b"\x01")
        session.push_outgoing(b"\x02")
        assert not session._drained.is_set()

        drained = asyncio.create_task(session.wait_drained())
        await asyncio.sleep(0.01)
        assert not drained.done()  # ничего ещё не забрано мостом

        session.mark_outgoing_taken()
        await asyncio.sleep(0.01)
        assert not drained.done()  # один из двух чанков ещё не забран

        session.mark_outgoing_taken()
        await asyncio.wait_for(drained, timeout=1.0)
        assert drained.done()
    finally:
        remove_session("call-drained-1")


@pytest.mark.asyncio
async def test_paced_frames_marks_chunks_taken_as_it_consumes_them() -> None:
    session = create_session("call-drained-2")
    try:
        session.push_outgoing(b"\x01" * 320)  # ровно 1 кадр @ 20ms/8kHz
        session.outgoing.put_nowait(None)

        frames = [f async for f in paced_frames(session, frame_ms=20, sample_rate=8000)]

        assert len(frames) == 1
        assert session._drained.is_set()
    finally:
        remove_session("call-drained-2")


def test_request_hangup_sets_event() -> None:
    session = create_session("call-hangup")
    try:
        assert not session.hangup_requested.is_set()
        session.request_hangup()
        assert session.hangup_requested.is_set()
    finally:
        remove_session("call-hangup")


def test_create_get_remove_session() -> None:
    session = create_session("call-a")
    try:
        assert get_session("call-a") is session
        remove_session("call-a")
        assert get_session("call-a") is None
    finally:
        remove_session("call-a")  # idempotent, на случай падения ассерта выше


def test_remove_unknown_session_is_noop() -> None:
    remove_session("never-existed")  # не должно падать


@pytest.mark.asyncio
async def test_wait_for_session_returns_existing_immediately() -> None:
    session = create_session("call-b")
    try:
        found = await wait_for_session("call-b", timeout=1.0)
        assert found is session
    finally:
        remove_session("call-b")


@pytest.mark.asyncio
async def test_wait_for_session_sees_session_created_concurrently() -> None:
    async def create_later() -> None:
        await asyncio.sleep(0.05)
        create_session("call-c")

    task = asyncio.create_task(create_later())
    try:
        found = await wait_for_session("call-c", timeout=2.0, poll_interval=0.01)
        assert found is not None
        assert found.call_id == "call-c"
    finally:
        await task
        remove_session("call-c")


@pytest.mark.asyncio
async def test_wait_for_session_times_out() -> None:
    found = await wait_for_session("never-created", timeout=0.1, poll_interval=0.02)
    assert found is None


@pytest.mark.asyncio
async def test_paced_frames_slices_into_fixed_size_frames() -> None:
    session = create_session("call-d")
    try:
        # 8000 Hz, 20ms -> 320 байт/кадр. Кладём 2.5 кадра одним чанком.
        session.push_outgoing(b"\x01" * 800)
        session.outgoing.put_nowait(None)  # закрыть после отправки

        frames = [
            frame
            async for frame in paced_frames(session, frame_ms=20, sample_rate=8000)
        ]

        # 800 байт = 2 полных кадра по 320 + хвост 160, добитый тишиной до 320.
        assert len(frames) == 3
        assert all(len(f) == 320 for f in frames)
        assert frames[2] == b"\x01" * 160 + b"\x00" * 160
    finally:
        remove_session("call-d")


@pytest.mark.asyncio
async def test_paced_frames_respects_real_timing() -> None:
    session = create_session("call-e")
    try:
        # 3 кадра по 20ms — итоговая пауза между первым и последним ~40ms.
        session.push_outgoing(b"\x02" * (320 * 3))
        session.outgoing.put_nowait(None)

        start = time.monotonic()
        frames = [
            frame
            async for frame in paced_frames(session, frame_ms=20, sample_rate=8000)
        ]
        elapsed = time.monotonic() - start

        assert len(frames) == 3
        # Не проверяем точный тайминг (флаки на CI) — только что пейсинг
        # реально тормозит поток, а не отдаёт всё мгновенно.
        assert elapsed >= 0.03
    finally:
        remove_session("call-e")


@pytest.mark.asyncio
async def test_paced_frames_empty_session_yields_nothing() -> None:
    session = create_session("call-f")
    try:
        session.outgoing.put_nowait(None)
        frames = [
            frame
            async for frame in paced_frames(session, frame_ms=20, sample_rate=8000)
        ]
        assert frames == []
    finally:
        remove_session("call-f")


@pytest.mark.asyncio
async def test_paced_frames_sends_keepalive_silence_when_idle() -> None:
    """Asterisk AudioSocket рвёт канал после 2000ms без активности на
    сокете (см. audiosocket_server.AUDIOSOCKET_IDLE_KEEPALIVE_MS) — при
    простое (клиент молчит, нам нечего сказать) paced_frames должен сам
    подавать тишину с интервалом idle_keepalive_ms, а не просто висеть."""
    session = create_session("call-keepalive-1")
    try:
        gen = paced_frames(session, frame_ms=20, sample_rate=8000, idle_keepalive_ms=30)
        frame1 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        frame2 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert frame1 == b"\x00" * 320
        assert frame2 == b"\x00" * 320
    finally:
        remove_session("call-keepalive-1")


@pytest.mark.asyncio
async def test_paced_frames_real_audio_preempts_keepalive() -> None:
    session = create_session("call-keepalive-2")
    try:
        gen = paced_frames(session, frame_ms=20, sample_rate=8000, idle_keepalive_ms=500)
        session.push_outgoing(b"\x07" * 320)
        # Реальные данные пришли задолго до keepalive-интервала — должны
        # выйти как есть, не подмениться тишиной.
        frame = await asyncio.wait_for(gen.__anext__(), timeout=0.2)
        assert frame == b"\x07" * 320
    finally:
        remove_session("call-keepalive-2")


@pytest.mark.asyncio
async def test_paced_frames_without_keepalive_blocks_indefinitely_on_idle() -> None:
    """Без idle_keepalive_ms (Voximplant-путь) поведение не меняется —
    просто висим на пустой очереди, тишину сами не изобретаем."""
    session = create_session("call-keepalive-3")
    try:
        gen = paced_frames(session, frame_ms=20, sample_rate=8000)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(gen.__anext__(), timeout=0.1)
    finally:
        remove_session("call-keepalive-3")
