"""Тесты AsteriskTelephonyProvider (REST-часть через ARI, мок httpx через respx).

Событийный WS-пайп (_pump_events/connect) тут не гоняем — это требует живого
ARI WebSocket-сервера и по сути интеграционный тест. Но код, который он
дёргает (_handle_ari_event, _continue_to_bridge), тестируем напрямую как
обычные async-методы — они не зависят от того, что источник событий WS.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from phoneagent.config import get_settings
from phoneagent.core.audio_session import create_session, remove_session
from phoneagent.models.call import CallStatusEnum
from phoneagent.providers.telephony.asterisk import AsteriskTelephonyProvider


def _asterisk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISK_HOST", "asterisk-test")
    monkeypatch.setenv("ASTERISK_ARI_PORT", "8088")
    monkeypatch.setenv("ASTERISK_ARI_USERNAME", "phoneagent")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "secret")
    monkeypatch.setenv("ASTERISK_STASIS_APP", "phoneagent_bridge")
    monkeypatch.setenv("ASTERISK_TRUNK_ENDPOINT", "mts_exolve")
    get_settings.cache_clear()


@pytest.fixture
async def provider(monkeypatch: pytest.MonkeyPatch) -> AsteriskTelephonyProvider:
    _asterisk_env(monkeypatch)
    p = AsteriskTelephonyProvider()
    p._client = httpx.AsyncClient(base_url=p._ari_base_url)
    yield p
    await p._client.aclose()


@pytest.mark.asyncio
async def test_make_call_originates_via_ari(provider: AsteriskTelephonyProvider) -> None:
    with respx.mock(base_url=provider._ari_base_url) as mock:
        route = mock.post("/channels").mock(
            return_value=httpx.Response(200, json={"id": "asterisk-chan-1"})
        )
        ref = await provider.make_call("+79991234567")

    assert ref.provider == "asterisk"
    assert ref.phone == "+79991234567"
    assert ref.metadata["ari_channel_id"] == "asterisk-chan-1"
    assert ref.metadata["trunk"] == "mts_exolve"
    assert provider._channels[ref.call_id] == "asterisk-chan-1"

    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["endpoint"] == "PJSIP/+79991234567@mts_exolve"
    assert body["app"] == "phoneagent_bridge"
    assert body["variables"]["PHONEAGENT_CALL_ID"] == ref.call_id


@pytest.mark.asyncio
async def test_hangup_deletes_channel(provider: AsteriskTelephonyProvider) -> None:
    provider._channels["call-1"] = "chan-1"
    with respx.mock(base_url=provider._ari_base_url) as mock:
        mock.delete("/channels/chan-1").mock(return_value=httpx.Response(204))
        await provider.hangup("call-1")

    assert "call-1" not in provider._channels


@pytest.mark.asyncio
async def test_hangup_unknown_call_is_noop(provider: AsteriskTelephonyProvider) -> None:
    # Не должно падать и не должно бить в сеть — просто лог warning.
    await provider.hangup("never-existed")


@pytest.mark.asyncio
async def test_hangup_tolerates_already_gone_channel(provider: AsteriskTelephonyProvider) -> None:
    provider._channels["call-1"] = "chan-1"
    with respx.mock(base_url=provider._ari_base_url) as mock:
        mock.delete("/channels/chan-1").mock(return_value=httpx.Response(404))
        await provider.hangup("call-1")  # не должно бросить исключение


@pytest.mark.asyncio
async def test_get_status_maps_ari_state(provider: AsteriskTelephonyProvider) -> None:
    provider._channels["call-1"] = "chan-1"
    with respx.mock(base_url=provider._ari_base_url) as mock:
        mock.get("/channels/chan-1").mock(
            return_value=httpx.Response(200, json={"state": "Up"})
        )
        status = await provider.get_status("call-1")

    assert status.status == CallStatusEnum.CONNECTED


@pytest.mark.asyncio
async def test_get_status_unknown_call_is_pending(provider: AsteriskTelephonyProvider) -> None:
    status = await provider.get_status("never-existed")
    assert status.status == CallStatusEnum.PENDING


@pytest.mark.asyncio
async def test_get_status_gone_channel_is_completed(provider: AsteriskTelephonyProvider) -> None:
    provider._channels["call-1"] = "chan-1"
    with respx.mock(base_url=provider._ari_base_url) as mock:
        mock.get("/channels/chan-1").mock(return_value=httpx.Response(404))
        status = await provider.get_status("call-1")

    assert status.status == CallStatusEnum.COMPLETED


@pytest.mark.asyncio
async def test_stasis_start_continues_channel_and_emits_event(
    provider: AsteriskTelephonyProvider,
) -> None:
    with respx.mock(base_url=provider._ari_base_url) as mock:
        continue_route = mock.post("/channels/chan-1/continue").mock(
            return_value=httpx.Response(204)
        )
        await provider._handle_ari_event(
            {
                "type": "StasisStart",
                "channel": {
                    "id": "chan-1",
                    "channelvars": {"PHONEAGENT_CALL_ID": "call-1"},
                },
            }
        )

    assert continue_route.called
    assert provider._channels["call-1"] == "chan-1"
    event = provider._events.get_nowait()
    assert event.event_type == "started"
    assert event.call_id == "call-1"


@pytest.mark.asyncio
async def test_stasis_end_emits_ended_event(provider: AsteriskTelephonyProvider) -> None:
    provider._channels["call-1"] = "chan-1"
    await provider._handle_ari_event({"type": "StasisEnd", "channel": {"id": "chan-1"}})

    event = provider._events.get_nowait()
    assert event.event_type == "ended"
    assert event.call_id == "call-1"


@pytest.mark.asyncio
async def test_send_audio_pushes_chunks_into_audio_session(
    provider: AsteriskTelephonyProvider,
) -> None:
    session = create_session("call-1")
    try:

        async def _chunks():
            yield b"\x01\x02"
            yield b"\x03\x04"

        # send_audio() теперь не возвращается, пока мост не заберёт чанки
        # из очереди (wait_drained) — гоним "мост" параллельно, иначе тест
        # повиснет. Асимметрия с продакшеном: там это делает paced_frames.
        drained: list[bytes] = []

        async def _drain_two() -> None:
            for _ in range(2):
                chunk = await session.outgoing.get()
                session.mark_outgoing_taken()
                drained.append(chunk)

        drain_task = asyncio.create_task(_drain_two())
        await asyncio.wait_for(provider.send_audio("call-1", _chunks()), timeout=2.0)
        await drain_task

        assert drained == [b"\x01\x02", b"\x03\x04"]
    finally:
        remove_session("call-1")


@pytest.mark.asyncio
async def test_send_audio_without_session_logs_and_returns(
    provider: AsteriskTelephonyProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    # wait_for_session тут реально ждёт timeout — сокращаем, чтобы тест не висел.
    import phoneagent.providers.telephony.asterisk as asterisk_module

    async def _fast_wait_for_session(call_id: str, **_: object) -> None:
        return None

    monkeypatch.setattr(asterisk_module, "wait_for_session", _fast_wait_for_session)

    async def _chunks():
        yield b"\x01\x02"

    await provider.send_audio("never-connected", _chunks())  # не должно бросить
