"""Run the TCP examples against an emulator, without hardware."""

import pytest

from examples.explicit_controls import send_control
from examples.fresh_reads import inspect_device
from examples.prepared_movie_launch import prepare_or_send
from kaleidescape import message as messages
from kaleidescape.device import Device

from .emulator import Emulator

SERIAL = "00000000123A"
HANDLE = "26-0.0-S_c446c8e2"


@pytest.mark.asyncio
async def test_fresh_reads_example_uses_queries_only(
    device: Device, emulator: Emulator
):
    """The discovery example reads state and leaves its connection closed."""
    await inspect_device(device, discover=True)
    assert emulator.requests_named(messages.GetAvailableDevicesBySerialNumber.name)
    assert not emulator.requests_named(messages.PlayMovieExperimental.name)
    assert not device.is_connected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "request_cls"),
    [
        ("pause-on", messages.PauseOn),
        ("pause-off", messages.PauseOff),
        ("back", messages.Back),
        ("play-or-pause", messages.PlayOrPause),
        ("page-up", messages.PageUp),
        ("page-down", messages.PageDown),
        ("details", messages.Details),
        ("go-search", messages.GoSearch),
        ("menu-on", messages.MenuOn),
        ("menu-off", messages.MenuOff),
    ],
)
async def test_explicit_control_example_sends_only_selected_action(
    device: Device, emulator: Emulator, action: str, request_cls: type[messages.Request]
):
    """One explicit CLI action sends one matching protocol command."""
    await send_control(device, action)
    assert len(emulator.requests_named(request_cls.name)) == 1
    assert not device.is_connected


@pytest.mark.asyncio
async def test_invalid_control_action_never_connects(device: Device):
    """Reject unknown actions before any device connection is opened."""
    with pytest.raises(ValueError, match="Choose"):
        await send_control(device, "pause")
    assert not device.is_connected


@pytest.mark.asyncio
async def test_prepared_launch_example_requires_send_flag(
    device: Device, emulator: Emulator
):
    """Default preparation is read-only; explicit opt-in sends once."""
    await prepare_or_send(device, SERIAL, HANDLE)
    assert not emulator.requests_named(messages.PlayMovieExperimental.name)
    await prepare_or_send(device, SERIAL, HANDLE, send=True)
    assert len(emulator.requests_named(messages.PlayMovieExperimental.name)) == 1
    assert not device.is_connected
