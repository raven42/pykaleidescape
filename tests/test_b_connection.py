"""Tests for connection module."""

import asyncio
import errno
from unittest.mock import patch

import pytest

from kaleidescape import const
from kaleidescape import message as messages
from kaleidescape.connection import Connection
from kaleidescape.const import STATE_CONNECTED, STATE_DISCONNECTED, STATE_RECONNECTING
from kaleidescape.dispatcher import Dispatcher
from kaleidescape.error import RequestNotSentError, RequestOutcomeUnknownError
from kaleidescape.message import Response

from . import create_signal
from .emulator import Emulator, NoResponse

# pylint: disable=unused-argument,missing-function-docstring,protected-access

# Never reached: the tests using it replace asyncio.open_connection.
UNUSED_PORT = 10000


@pytest.mark.asyncio
async def test_init():
    """Test a new connection is disconnected and disconnecting it is a no-op."""
    dispatcher = Dispatcher()
    events: list[str] = []
    dispatcher.connect(lambda event, *args: events.append(event))
    connection = Connection(dispatcher)
    assert connection.dispatcher is dispatcher
    assert connection.state == STATE_DISCONNECTED

    await connection.disconnect()
    await asyncio.sleep(0)
    assert connection.state == STATE_DISCONNECTED
    assert not events


@pytest.mark.asyncio
@pytest.mark.parametrize("reconnect", [False, True])
async def test_connect_refused(emulator: Emulator, reconnect: bool):
    """Test the initial connect raises when refused, even with reconnect enabled."""
    await emulator.stop()
    connection = Connection(Dispatcher())
    with pytest.raises(ConnectionRefusedError):
        await connection.connect(
            "127.0.0.1", port=emulator.port, timeout=1, reconnect=reconnect
        )
    assert connection.state == STATE_DISCONNECTED


@pytest.mark.asyncio
@pytest.mark.parametrize("reconnect", [False, True])
async def test_connect_timeout(reconnect: bool):
    """Test the initial connect raises ConnectionError when it times out."""

    async def never_connects(*args, **kwargs):
        await asyncio.Event().wait()

    connection = Connection(Dispatcher())
    with (
        patch("asyncio.open_connection", never_connects),
        pytest.raises(ConnectionError) as err,
    ):
        await connection.connect(
            "127.0.0.1", port=UNUSED_PORT, timeout=0.1, reconnect=reconnect
        )
    assert isinstance(err.value.__cause__, TimeoutError)
    assert connection.state == STATE_DISCONNECTED


@pytest.mark.asyncio
async def test_connect_os_error():
    """Test OS errors that are not ConnectionErrors are raised as ConnectionError."""

    async def unreachable(*args, **kwargs):
        raise OSError(errno.EHOSTUNREACH, "No route to host")

    connection = Connection(Dispatcher())
    with (
        patch("asyncio.open_connection", unreachable),
        pytest.raises(ConnectionError) as err,
    ):
        await connection.connect("127.0.0.1", port=UNUSED_PORT, timeout=1)
    assert isinstance(err.value.__cause__, OSError)
    assert "No route to host" in str(err.value)
    assert connection.state == STATE_DISCONNECTED


@pytest.mark.asyncio
async def test_connect_succeeds(emulator: Emulator):
    """Test connect stores its settings and does not dispatch STATE_CONNECTED."""
    dispatcher = Dispatcher()
    events: list[str] = []
    dispatcher.connect(lambda event, *args: events.append(event))
    connection = Connection(dispatcher)

    await connection.connect("127.0.0.1", port=emulator.port, timeout=1)
    await asyncio.sleep(0)
    assert connection.ip == "127.0.0.1"
    assert connection.port == emulator.port
    assert connection.timeout == 1
    assert connection.state == STATE_CONNECTED
    assert not events

    await connection.disconnect()


@pytest.mark.asyncio
async def test_manual_disconnect(connection: Connection):
    """Test disconnect updates state and emits signal."""
    signal = create_signal(connection.dispatcher, STATE_DISCONNECTED)
    await connection.disconnect()
    await signal.wait()
    assert connection.state == STATE_DISCONNECTED


@pytest.mark.asyncio
async def test_server_disconnect(emulator: Emulator, connection: Connection):
    """Test the server dropping the connection disconnects when reconnect is off."""
    assert connection.state == STATE_CONNECTED
    signal = create_signal(connection.dispatcher, STATE_DISCONNECTED)
    await emulator.wait_for_client()
    await emulator.stop()
    await signal.wait()
    assert connection.state == STATE_DISCONNECTED


@pytest.mark.asyncio
async def test_reconnect(emulator: Emulator):
    """Test a dropped connection reconnects, then calls on_reconnect."""
    dispatcher = Dispatcher()
    on_reconnect_called = asyncio.Event()

    async def on_reconnect():
        on_reconnect_called.set()

    connection = Connection(dispatcher, on_reconnect=on_reconnect)
    connect_signal = create_signal(dispatcher, STATE_CONNECTED)
    disconnect_signal = create_signal(dispatcher, STATE_DISCONNECTED)

    await connection.connect(
        "127.0.0.1", port=emulator.port, timeout=1, reconnect=True, reconnect_delay=0.1
    )
    assert connection.state == STATE_CONNECTED

    await emulator.wait_for_client()
    await emulator.stop()
    await disconnect_signal.wait()
    assert connection.state == STATE_RECONNECTING
    assert not on_reconnect_called.is_set()

    await emulator.start()
    await connect_signal.wait()
    assert connection.state == STATE_CONNECTED
    assert on_reconnect_called.is_set()

    await connection.disconnect()


@pytest.mark.asyncio
async def test_reconnect_cancelled(emulator: Emulator):
    """Test disconnect cancels a pending reconnect for good."""
    dispatcher = Dispatcher()
    connection = Connection(dispatcher)
    disconnect_signal = create_signal(dispatcher, STATE_DISCONNECTED)

    await connection.connect(
        "127.0.0.1", port=emulator.port, timeout=1, reconnect=True, reconnect_delay=0.1
    )
    await emulator.wait_for_client()
    await emulator.stop()
    await disconnect_signal.wait()
    assert connection.state == STATE_RECONNECTING

    await connection.disconnect()
    assert connection.state == STATE_DISCONNECTED

    # Leave time for several reconnect attempts had the reconnect not been cancelled.
    await emulator.start()
    await asyncio.sleep(0.3)
    assert connection.state == STATE_DISCONNECTED


@pytest.mark.asyncio
async def test_unhandled_exception_triggers_reconnect(emulator: Emulator):
    """Test that unhandled exceptions in _response_handler trigger reconnect."""
    dispatcher = Dispatcher()
    connection = Connection(dispatcher)
    states: list[str] = []
    connect_signal = create_signal(dispatcher, STATE_CONNECTED)
    disconnect_signal = create_signal(dispatcher, STATE_DISCONNECTED)

    # A sync listener records the state as the signal is dispatched, before the
    # reconnect task can change it.
    def on_disconnect(event, *args):
        if event == STATE_DISCONNECTED:
            states.append(connection.state)

    dispatcher.connect(on_disconnect)

    await connection.connect(
        "127.0.0.1", port=emulator.port, timeout=1, reconnect=True, reconnect_delay=0.1
    )
    await emulator.wait_for_client()

    with patch.object(Response, "factory", side_effect=RuntimeError("simulated")):
        await emulator.send_event(["01"], 0, "DEVICE_POWER_STATE", ["0"])
        await disconnect_signal.wait()

    assert states == [STATE_RECONNECTING]

    await connect_signal.wait()
    assert connection.state == STATE_CONNECTED

    await connection.disconnect()


@pytest.mark.asyncio
async def test_wrong_response_name_retires_stream(emulator: Emulator):
    emulator.register_mock_command(
        ("01",),
        messages.GetAvailableDevices.name,
        (const.SUCCESS, "WRONG_NAME", ["01"]),
    )
    connection = Connection(Dispatcher())
    await connection.connect("127.0.0.1", emulator.port, 0.2)
    with pytest.raises(RequestOutcomeUnknownError):
        await messages.GetAvailableDevices().send(connection)
    await asyncio.sleep(0)
    assert connection.state == const.STATE_DISCONNECTED


@pytest.mark.asyncio
async def test_wrong_target_cannot_acknowledge_pending_request(emulator: Emulator):
    connection = Connection(Dispatcher())
    await connection.connect("127.0.0.1", emulator.port, 0.2)
    task = asyncio.create_task(NoResponse().send(connection))
    seq = (await emulator.wait_for_request(NoResponse.name)).seq
    await emulator.send_reply("02", seq)
    with pytest.raises(RequestOutcomeUnknownError):
        await task
    await asyncio.sleep(0)
    assert connection.state == const.STATE_DISCONNECTED


@pytest.mark.asyncio
async def test_disconnect_wakes_pending_request(emulator: Emulator):
    connection = Connection(Dispatcher())
    await connection.connect("127.0.0.1", emulator.port, 1)
    task = asyncio.create_task(NoResponse().send(connection))
    await emulator.wait_for_request(NoResponse.name)
    await connection.disconnect()
    with pytest.raises(RequestOutcomeUnknownError):
        await asyncio.wait_for(task, 0.1)


@pytest.mark.asyncio
async def test_cancel_after_write_retires_stream(emulator: Emulator):
    connection = Connection(Dispatcher())
    await connection.connect("127.0.0.1", emulator.port, 1)
    task = asyncio.create_task(NoResponse().send(connection))
    await emulator.wait_for_request(NoResponse.name)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert connection.state == const.STATE_DISCONNECTED


@pytest.mark.asyncio
async def test_disconnected_request_is_definitely_unsent(emulator: Emulator):
    connection = Connection(Dispatcher())
    with pytest.raises(RequestNotSentError):
        await messages.Play().send(connection)
    assert not emulator.requests_named(messages.Play.name)


@pytest.mark.asyncio
async def test_event_callback_failure_does_not_close_connection(emulator: Emulator):
    seen = asyncio.Event()

    async def failing_event(_response):
        seen.set()
        raise RuntimeError("fictional callback failure")

    connection = Connection(Dispatcher(), on_event=failing_event)
    await connection.connect("127.0.0.1", emulator.port, 1)
    await emulator.wait_for_client()
    await emulator.send_event(["01"], const.SUCCESS, messages.PlayStatus.name, ["0"])
    await asyncio.wait_for(seen.wait(), 1)
    assert connection.state == const.STATE_CONNECTED
    assert (await messages.GetAvailableDevices().send(connection))[
        0
    ].name == messages.AvailableDevices.name
    await connection.disconnect()


@pytest.mark.asyncio
async def test_disconnect_cancels_owned_event_tasks(emulator: Emulator):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def waiting_event(_response):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    connection = Connection(Dispatcher(), on_event=waiting_event)
    await connection.connect("127.0.0.1", emulator.port, 1)
    await emulator.wait_for_client()
    await emulator.send_event(["01"], const.SUCCESS, messages.PlayStatus.name, ["0"])
    await asyncio.wait_for(started.wait(), 1)
    await connection.disconnect()
    assert cancelled.is_set()
    assert not connection._event_tasks
