"""Opt-in tests against an owned player or server on a private network.

This directory is excluded from default pytest recursion. Run an individual test
explicitly with PYK_HARDWARE=1 and the variables described in its patch readme.
"""

import os

import pytest

from kaleidescape import const
from kaleidescape.device import Device


def _required(*names: str) -> list[str]:
    """Skip when hardware testing or the selected device is not configured."""
    if os.getenv("PYK_HARDWARE") != "1":
        pytest.skip("Set PYK_HARDWARE=1 for owned hardware tests")
    values = [os.getenv(name) for name in names]
    if any(not value for value in values):
        pytest.skip("The selected device's host and serial are required")
    return [value for value in values if value is not None]


@pytest.mark.asyncio
async def test_player_read_only():
    """Verify identity, movie capability, fresh status, and discovery."""
    host, serial = _required("PYK_PLAYER_HOST", "PYK_PLAYER_SERIAL")
    player = Device(host, reconnect=False)
    try:
        await player.connect()
        identity = await player.read_identity()
        assert identity.serial_number == serial.upper()
        assert identity.model
        assert identity.movie_zones >= 1
        state = await player.read_control_state()
        assert state.identity.serial_number == identity.serial_number
        discovered = await player.discover_devices()
        assert serial.upper() in {item.serial_number for item in discovered}
        await player.get_highlighted_selection()
    finally:
        await player.disconnect()


@pytest.mark.asyncio
async def test_server_read_only():
    """Verify server identity and zero movie zones independently of a player."""
    host, serial = _required("PYK_SERVER_HOST", "PYK_SERVER_SERIAL")
    server = Device(host, reconnect=False)
    try:
        await server.connect()
        identity = await server.read_identity()
        assert identity.serial_number == serial.upper()
        assert identity.model
        assert identity.movie_zones == 0
        state = await server.read_control_state()
        assert state.identity.serial_number == serial.upper()
    finally:
        await server.disconnect()


@pytest.mark.asyncio
async def test_explicit_pause_resume():
    """Confirm explicit pause and resume never toggle an already reached state."""
    host, serial = _required("PYK_PLAYER_HOST", "PYK_PLAYER_SERIAL")
    if os.getenv("PYK_HARDWARE_CONTROLS") != "1":
        pytest.skip("Enable controls during an agreed playback session")
    player = Device(host, reconnect=False)
    try:
        await player.connect()
        if (
            await player.read_control_state(serial)
        ).play_status != const.PLAY_STATUS_PLAYING:
            pytest.skip("Start known playback before testing pause/resume")
        await player.pause_on()
        await player.pause_on()
        assert (
            await player.read_control_state(serial)
        ).play_status == const.PLAY_STATUS_PAUSED
        await player.pause_off()
        await player.pause_off()
        assert (
            await player.read_control_state(serial)
        ).play_status == const.PLAY_STATUS_PLAYING
    finally:
        await player.disconnect()


@pytest.mark.asyncio
async def test_back_navigation_acknowledged():
    """Send BACK once only when an operator has prepared a safe menu state."""
    host, _ = _required("PYK_PLAYER_HOST", "PYK_PLAYER_SERIAL")
    if os.getenv("PYK_HARDWARE_BACK") != "1":
        pytest.skip("Enable BACK during an agreed navigation session")
    player = Device(host, reconnect=False)
    try:
        await player.connect()
        await player.back()
    finally:
        await player.disconnect()


@pytest.mark.asyncio
async def test_movie_launch_preparation():
    """Validate a known exact movie handle without sending PLAY_MOVIE."""
    host, serial = _required("PYK_PLAYER_HOST", "PYK_PLAYER_SERIAL")
    handle = os.getenv("PYK_HARDWARE_LAUNCH_HANDLE")
    if not handle:
        pytest.skip("Set a known qualified handle for read-only preparation")
    player = Device(host, reconnect=False)
    try:
        await player.connect()
        prepared = await player.prepare_movie_launch(handle, serial=serial)
        assert prepared.handle == handle
        assert prepared.title
    finally:
        await player.disconnect()


@pytest.mark.asyncio
async def test_movie_launch_send_once():
    """Send one undocumented launch only with an explicit operator opt-in."""
    host, serial = _required("PYK_PLAYER_HOST", "PYK_PLAYER_SERIAL")
    handle = os.getenv("PYK_HARDWARE_LAUNCH_HANDLE")
    if not handle or os.getenv("PYK_HARDWARE_LAUNCH") != "1":
        pytest.skip("Set a known handle and enable one experimental launch")
    player = Device(host, reconnect=False)
    try:
        await player.connect()
        prepared = await player.prepare_movie_launch(handle, serial=serial)
        acknowledgement = await player.send_movie_launch(
            prepared, allow_experimental=True
        )
        assert acknowledgement.status == const.SUCCESS
        await player.read_control_state(serial)
        # ACK and state observation do not prove an exact edition started.
    finally:
        await player.disconnect()
