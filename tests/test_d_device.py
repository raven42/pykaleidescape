"""Tests for device module."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest

from kaleidescape import const
from kaleidescape import message as messages
from kaleidescape.connection import Connection
from kaleidescape.const import ERROR_INCOMPATIBLE_VIDEO_CONFIG, LOCAL_CPDID, SUCCESS
from kaleidescape.device import Device, MovieLaunchProfile
from kaleidescape.dispatcher import Dispatcher
from kaleidescape.error import KaleidescapeError

from . import create_signal
from .emulator import Emulator

# pylint: disable=unused-argument,missing-function-docstring,protected-access

CONTENT_HANDLE = "26-0.0-S_c446c8e2"


@pytest.mark.asyncio
async def test_server_only_refresh_skips_movie_queries(
    device: Device, emulator: Emulator
):
    """A powered-on server has no movie zone to refresh."""
    emulator.register_mock_command(
        ("01",),
        messages.GetNumZones.name,
        (SUCCESS, messages.NumZones.name, ["0", "0"]),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetDevicePowerState.name,
        (SUCCESS, messages.DevicePowerState.name, ["1", "0"]),
    )
    await device.connect()
    assert device.is_server_only
    await device.refresh()
    assert not emulator.requests_named(messages.GetUiState.name)


def param_id(value: object) -> str | None:
    """Names parametrized methods and classes in test ids."""
    return getattr(value, "__name__", None)


def play_status_fields(status: str) -> list[str]:
    """Return PLAY_STATUS fields with the given play status and defaults elsewhere."""
    return [status, "1", "00", "00000", "00000", "000", "00000", "00000"]


def mock_power_on(emulator: Emulator) -> None:
    """Make the emulator report the device as powered on."""
    emulator.register_mock_command(
        ("01",),
        messages.GetDevicePowerState.name,
        (SUCCESS, messages.DevicePowerState.name, ["1", "1"]),
    )


async def send_play_status(device: Device, emulator: Emulator, status: str) -> None:
    """Send a PLAY_STATUS event and wait for the device to handle it."""
    signal = create_signal(device.dispatcher, messages.PlayStatus.name)
    await emulator.send_event(
        [LOCAL_CPDID], SUCCESS, messages.PlayStatus.name, play_status_fields(status)
    )
    await asyncio.wait_for(signal.wait(), 1)


@pytest.mark.asyncio
async def test_init(emulator: Emulator, device: Device):
    """Test initialization of device."""
    assert isinstance(device.connection, Connection)
    assert isinstance(device.dispatcher, Dispatcher)
    assert device.host == "127.0.0.1"
    assert device.port == emulator.port
    assert device.is_connected is False
    assert device.serial_number == ""


@pytest.mark.asyncio
async def test_connect(device: Device):
    """Test connect loads system and power state, and disconnect disconnects."""
    await device.connect()
    assert device.is_connected
    assert device.system.serial_number == "00000000123A"
    assert device.serial_number == "00000000123A"
    assert device.system.cpdid == ""
    assert device.system.ip_address == "127.0.0.1"
    assert device.system.protocol == 16
    assert device.system.kos_version == "10.4.2-19218"
    assert device.system.type == "Strato S"
    assert device.system.friendly_name == "Theater"
    assert device.system.movie_zones == 1
    assert device.system.music_zones == 1
    assert device.power.state == const.DEVICE_POWER_STATE_STANDBY
    assert device.power.readiness == const.SYSTEM_READINESS_STATE_IDLE
    assert device.is_server_only is False
    assert device.is_movie_player is True

    await device.disconnect()
    assert not device.is_connected


@pytest.mark.asyncio
async def test_disconnect_during_reconnect(emulator: Emulator):
    """Test Device.disconnect() cancels reconnect in STATE_RECONNECTING."""
    device = Device(
        "127.0.0.1", port=emulator.port, reconnect=True, reconnect_delay=0.1
    )
    disconnect_signal = create_signal(device.dispatcher, const.STATE_DISCONNECTED)

    await device.connect()
    assert device.is_connected

    await emulator.wait_for_client()
    await emulator.stop()
    await disconnect_signal.wait()
    assert device.connection.state == const.STATE_RECONNECTING

    await device.disconnect()
    assert device.connection.state == const.STATE_DISCONNECTED
    assert not device.is_connected


@pytest.mark.asyncio
async def test_connect_sends_syslog_identification(emulator: Emulator, device: Device):
    """Test connect sends the version from __version__ to syslog.

    importlib.metadata fails in PyInstaller bundles, so the version must come from
    the module attribute. Patching it to a sentinel proves the syslog message uses it.
    """
    sentinel = "0.0.0-test-sentinel"
    with patch("kaleidescape.device.__version__", sentinel):
        await device.connect()

    syslog_requests = emulator.requests_named(messages.SendToSyslog.name)
    assert len(syslog_requests) == 1
    assert syslog_requests[0].fields == [
        "INFORMATION",
        f"pykaleidescape version {sentinel}",
    ]


@pytest.mark.asyncio
async def test_connect_while_playing_loads_playback_state(
    emulator: Emulator, device: Device
):
    """Test connecting to a device that is already playing loads playback state."""
    mock_power_on(emulator)
    emulator.register_mock_command(
        ("01",),
        messages.GetHighlightedSelection.name,
        (SUCCESS, messages.HighlightedSelection.name, [CONTENT_HANDLE]),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetPlayStatus.name,
        (SUCCESS, messages.PlayStatus.name, play_status_fields("2")),
    )

    await device.connect()

    assert len(emulator.requests_named(messages.GetUiState.name)) == 1
    assert device.power.state == const.DEVICE_POWER_STATE_ON
    assert device.osd.ui_screen == const.UI_STATE_SCREEN_MOVIE_LIST
    assert device.automation.movie_location == const.MOVIE_LOCATION_CONTENT
    assert device.movie.play_status == const.PLAY_STATUS_PLAYING
    assert device.movie.title == "Turtle Odyssey"


@pytest.mark.asyncio
async def test_get_available_devices(device: Device):
    """Test get available devices."""
    await device.connect()
    fields = await device.get_available_devices()
    assert fields == [LOCAL_CPDID]


@pytest.mark.asyncio
async def test_get_available_serial_numbers(device: Device):
    """Test get available serial numbers."""
    await device.connect()
    fields = await device.get_available_serial_numbers()
    assert fields == ["00000000123A"]


@pytest.mark.asyncio
async def test_refresh_in_standby(emulator: Emulator, device: Device):
    """Test refresh connects but skips playback queries while in standby."""
    await device.refresh()
    assert device.is_connected
    assert device.power.state == const.DEVICE_POWER_STATE_STANDBY
    assert not emulator.requests_named(messages.GetUiState.name)


@pytest.mark.asyncio
async def test_refresh_before_connect_queries_once(emulator: Emulator, device: Device):
    """Test refresh on an unconnected device connects and queries playback once."""
    mock_power_on(emulator)

    await device.refresh()

    assert device.is_connected
    assert len(emulator.requests_named(messages.GetUiState.name)) == 1


@pytest.mark.asyncio
async def test_power_on_event_refreshes_state(emulator: Emulator, device: Device):
    """Test a power-on event updates power state and refreshes playback state."""
    await device.refresh()

    assert device.power.state == const.DEVICE_POWER_STATE_STANDBY
    assert device.osd.ui_screen == const.UI_STATE_SCREEN_UNKNOWN
    assert device.automation.movie_location == const.MOVIE_LOCATION_NONE

    signal = create_signal(device.dispatcher, messages.DevicePowerState.name)
    await emulator.send_event(
        [LOCAL_CPDID], SUCCESS, messages.DevicePowerState.name, ["1"]
    )
    await asyncio.wait_for(signal.wait(), 1)

    assert device.power.state == const.DEVICE_POWER_STATE_ON
    assert device.osd.ui_screen == const.UI_STATE_SCREEN_MOVIE_LIST
    assert device.automation.movie_location == const.MOVIE_LOCATION_CONTENT


@pytest.mark.asyncio
async def test_events(emulator: Emulator, device: Device):
    """Test an event updates device state and is dispatched to listeners."""
    await device.connect()
    assert device.power.readiness == const.SYSTEM_READINESS_STATE_IDLE

    signal = create_signal(device.dispatcher, messages.SystemReadinessState.name)
    await emulator.send_event(
        [LOCAL_CPDID], SUCCESS, messages.SystemReadinessState.name, ["0"]
    )
    await asyncio.wait_for(signal.wait(), 1)
    assert device.power.readiness == const.SYSTEM_READINESS_STATE_READY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "request_type"),
    [
        (Device.leave_standby, messages.LeaveStandby),
        (Device.enter_standby, messages.EnterStandby),
        (Device.play, messages.Play),
        (Device.pause, messages.Pause),
        (Device.play_or_pause, messages.PlayOrPause),
        (Device.pause_on, messages.PauseOn),
        (Device.pause_off, messages.PauseOff),
        (Device.page_up, messages.PageUp),
        (Device.page_down, messages.PageDown),
        (Device.back, messages.Back),
        (Device.details, messages.Details),
        (Device.go_search, messages.GoSearch),
        (Device.menu_on, messages.MenuOn),
        (Device.menu_off, messages.MenuOff),
        (Device.stop, messages.Stop),
        (Device.next, messages.Next),
        (Device.previous, messages.Previous),
        (Device.replay, messages.Replay),
        (Device.scan_forward, messages.ScanForward),
        (Device.scan_reverse, messages.ScanReverse),
        (Device.select, messages.Select),
        (Device.up, messages.Up),
        (Device.down, messages.Down),
        (Device.left, messages.Left),
        (Device.right, messages.Right),
        (Device.cancel, messages.Cancel),
        (Device.status_and_settings, messages.StatusAndSettings),
        (Device.intermission_toggle, messages.IntermissionToggle),
        (Device.go_movie_list, messages.GoMovieList),
        (Device.go_movie_collections, messages.GoMovieCollections),
        (Device.go_movies, messages.GoMovies),
        (Device.go_movie_covers, messages.GoMovieCovers),
        (Device.menu_toggle, messages.MenuToggle),
    ],
    ids=param_id,
)
async def test_commands(
    emulator: Emulator,
    device: Device,
    method: Callable[[Device], Awaitable[None]],
    request_type: type[messages.Request],
):
    """Test each command method sends its protocol command."""
    await device.connect()

    await method(device)

    sent = emulator.requests_named(request_type.name)
    assert len(sent) == 1
    assert sent[0].fields == []


@pytest.mark.asyncio
async def test_get_content_details(device: Device):
    """Test command involving multiline response."""
    await device.refresh()
    res = await device.get_content_details(CONTENT_HANDLE)
    assert res.field_handle == CONTENT_HANDLE
    assert res.field_table == "movies"
    assert res.field_title == "Turtle Odyssey"


@pytest.mark.asyncio
async def test_play_status_with_empty_highlighted(emulator: Emulator, device: Device):
    """Test playback starting with no highlighted title skips content details."""
    mock_power_on(emulator)
    await device.refresh()
    assert device.power.state == const.DEVICE_POWER_STATE_ON
    assert device.osd.highlighted == ""

    await send_play_status(device, emulator, "2")

    assert device.movie.play_status == const.PLAY_STATUS_PLAYING
    assert not emulator.requests_named(messages.GetContentDetails.name)


@pytest.mark.asyncio
async def test_play_status_updates_content_details(emulator: Emulator, device: Device):
    """Test playback starting loads the title, and stopping clears it."""
    mock_power_on(emulator)
    emulator.register_mock_command(
        ("01",),
        messages.GetHighlightedSelection.name,
        (SUCCESS, messages.HighlightedSelection.name, [CONTENT_HANDLE]),
    )
    await device.refresh()
    assert device.osd.highlighted == CONTENT_HANDLE
    assert device.movie.title == ""

    await send_play_status(device, emulator, "2")
    assert device.movie.play_status == const.PLAY_STATUS_PLAYING
    assert device.movie.handle == CONTENT_HANDLE
    assert device.movie.title == "Turtle Odyssey"

    await send_play_status(device, emulator, "0")
    assert device.movie.play_status == const.PLAY_STATUS_NONE
    assert device.movie.handle == ""
    assert device.movie.title == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "value", "event"),
    [
        (Device.set_volume_capabilities, 0, "VOLUME_CAPABILITIES=0"),
        (Device.set_volume_capabilities, 31, "VOLUME_CAPABILITIES=31"),
        (Device.set_volume_level, 0, "VOLUME_LEVEL=0"),
        (Device.set_volume_level, 100, "VOLUME_LEVEL=100"),
        (Device.set_volume_muted, True, const.USER_DEFINED_EVENT_MUTE_ON_FB),
        (Device.set_volume_muted, False, const.USER_DEFINED_EVENT_MUTE_OFF_FB),
    ],
    ids=param_id,
)
async def test_volume_events(
    emulator: Emulator,
    device: Device,
    method: Callable[[Device, Any], Awaitable[None]],
    value: int | bool,
    event: str,
):
    """Test volume methods send the matching user defined event."""
    await device.connect()

    await method(device, value)

    sent = emulator.requests_named(messages.SendEvent.name)
    assert len(sent) == 1
    assert sent[0].fields == [event]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "value", "error"),
    [
        (Device.set_volume_capabilities, "15", TypeError),
        (Device.set_volume_capabilities, True, TypeError),
        (Device.set_volume_capabilities, -1, ValueError),
        (Device.set_volume_capabilities, 32, ValueError),
        (Device.set_volume_level, "50", TypeError),
        (Device.set_volume_level, True, TypeError),
        (Device.set_volume_level, -1, ValueError),
        (Device.set_volume_level, 101, ValueError),
        (Device.set_volume_muted, "true", TypeError),
        (Device.set_volume_muted, 1, TypeError),
    ],
    ids=param_id,
)
async def test_volume_events_invalid(
    emulator: Emulator,
    device: Device,
    method: Callable[[Device, Any], Awaitable[None]],
    value: object,
    error: type[Exception],
):
    """Test volume methods reject invalid values without sending anything."""
    await device.connect()

    with pytest.raises(error):
        await method(device, value)
    assert not emulator.requests_named(messages.SendEvent.name)


@pytest.mark.asyncio
async def test_refresh_after_reconnect(emulator: Emulator):
    """Test device state is refreshed after auto-reconnect."""
    device = Device(
        "127.0.0.1", port=emulator.port, reconnect=True, reconnect_delay=0.1
    )
    connect_signal = create_signal(device.dispatcher, const.STATE_CONNECTED)
    disconnect_signal = create_signal(device.dispatcher, const.STATE_DISCONNECTED)

    await device.connect()
    assert device.power.state == const.DEVICE_POWER_STATE_STANDBY

    mock_power_on(emulator)

    await emulator.wait_for_client()
    await emulator.stop()
    await disconnect_signal.wait()

    # on_reconnect runs to completion before STATE_CONNECTED is dispatched.
    await emulator.start()
    await connect_signal.wait()
    assert device.is_connected
    assert device.power.state == const.DEVICE_POWER_STATE_ON
    assert device.osd.ui_screen == const.UI_STATE_SCREEN_MOVIE_LIST

    await device.disconnect()


@pytest.mark.asyncio
async def test_refresh_partial_failure(emulator: Emulator, device: Device):
    """Test refresh applies the queries that succeed when another one fails.

    Devices without masking calibration or CinemaScope return error 028
    (Incompatible video configuration) for GET_SCREEN_MASK2.
    """
    emulator.register_mock_command(
        ("01",),
        messages.GetScreenMask2.name,
        (ERROR_INCOMPATIBLE_VIDEO_CONFIG,),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetPlayStatus.name,
        (SUCCESS, messages.PlayStatus.name, play_status_fields("1")),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetScreenMask.name,
        (SUCCESS, messages.ScreenMask.name, ["01", "000", "000", "05", "0000", "0000"]),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetCinemascapeMode.name,
        (SUCCESS, messages.CinemascapeMode.name, ["1"]),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetCinemascapeMask.name,
        (SUCCESS, messages.CinemascapeMask.name, ["175"]),
    )
    mock_power_on(emulator)

    await device.connect()
    assert device.power.state == const.DEVICE_POWER_STATE_ON

    await device.refresh()

    assert emulator.requests_named(messages.GetScreenMask2.name)
    assert device.osd.ui_screen == const.UI_STATE_SCREEN_MOVIE_LIST
    assert device.movie.play_status == const.PLAY_STATUS_PAUSED
    assert device.automation.movie_location == const.MOVIE_LOCATION_CONTENT
    assert device.automation.screen_mask_ratio == const.SCREEN_MASK_ASPECT_RATIO_133
    assert device.automation.cinemascape_mode == const.CINEMASCAPE_MODE_ANAMORPHIC
    # Fetched only after the gathered queries, so refresh carried on past the error.
    assert device.automation.cinemascape_mask == 175


@pytest.mark.asyncio
async def test_reconnect_rechecks_identity_before_reporting_ready(emulator: Emulator):
    device = Device("127.0.0.1", port=emulator.port, timeout=0.2, reconnect_delay=0.01)
    await device.connect()
    connected = create_signal(device.dispatcher, const.STATE_CONNECTED)
    await emulator.wait_for_client()
    await emulator.stop()
    emulator.register_mock_command(
        ("01",),
        messages.GetDeviceInfo.name,
        (
            const.SUCCESS,
            messages.DeviceInfo.name,
            ["", "00000000DEAD", "00", "127.0.0.1"],
        ),
    )
    await emulator.start()
    await asyncio.sleep(0.05)
    assert not connected.is_set()
    emulator.register_mock_command(
        ("01",),
        messages.GetDeviceInfo.name,
        (
            const.SUCCESS,
            messages.DeviceInfo.name,
            ["", "00000000123A", "00", "127.0.0.1"],
        ),
    )
    await asyncio.wait_for(connected.wait(), 1)
    assert device.serial_number == "00000000123A"
    await device.disconnect()


@pytest.mark.asyncio
async def test_discovery_reads_routed_identity_without_new_connections(
    device: Device, emulator: Emulator
):
    await device.connect()
    initial_generation = device.connection.generation
    identities = await device.discover_devices()
    assert len(identities) == 1
    assert identities[0].serial_number == "00000000123A"
    assert identities[0].movie_zones == 1
    assert identities[0].generation == initial_generation
    assert (
        emulator.requests_named(messages.GetDeviceInfo.name)[-1].device_id
        == "#00000000123A"
    )
    assert device.connection.generation == initial_generation


@pytest.mark.asyncio
async def test_identity_reads_protocol_width_device_info_serial(
    device: Device, emulator: Emulator
):
    # The discovery response uses 12 hex digits; DEVICE_INFO uses 16.
    emulator.register_mock_command(
        ("01", "#00000000123A"),
        messages.GetDeviceInfo.name,
        (
            const.SUCCESS,
            messages.DeviceInfo.name,
            ["", "000000000000123A", "00", "127.0.0.1"],
        ),
    )
    await device.connect()

    direct = await device.read_identity()
    routed = await device.read_identity("123A")
    discovered = await device.discover_devices()

    assert direct.serial_number == "00000000123A"
    assert routed.serial_number == direct.serial_number
    assert [identity.serial_number for identity in discovered] == [direct.serial_number]


@pytest.mark.asyncio
async def test_control_state_reads_actual_title_not_highlight(
    device: Device, emulator: Emulator
):
    emulator.register_mock_command(
        ("01",),
        messages.GetDevicePowerState.name,
        (const.SUCCESS, messages.DevicePowerState.name, ["1", "1"]),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetSystemReadinessState.name,
        (const.SUCCESS, messages.SystemReadinessState.name, ["0"]),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetPlayingTitleName.name,
        (const.SUCCESS, messages.PlayingTitleName.name, ["Now Playing"]),
    )
    await device.connect()
    state = await device.read_control_state()
    assert state.power == const.DEVICE_POWER_STATE_ON
    assert state.readiness == const.SYSTEM_READINESS_STATE_READY
    assert state.playing_title == "Now Playing"
    assert state.identity.serial_number == "00000000123A"


@pytest.mark.asyncio
async def test_identity_mismatch_rejected(device: Device, emulator: Emulator):
    await device.connect()
    emulator.register_mock_command(
        ("#00000000123A",),
        messages.GetDeviceInfo.name,
        (
            const.SUCCESS,
            messages.DeviceInfo.name,
            ["", "00000000DEAD", "00", "127.0.0.1"],
        ),
    )
    with pytest.raises(KaleidescapeError, match="identity mismatch"):
        await device.read_identity("00000000123A")


@pytest.mark.asyncio
async def test_server_only_connect_skips_movie_refresh(
    device: Device, emulator: Emulator
):
    emulator.register_mock_command(
        ("01",),
        messages.GetNumZones.name,
        (const.SUCCESS, messages.NumZones.name, ["0", "0"]),
    )
    emulator.register_mock_command(
        ("01",),
        messages.GetDevicePowerState.name,
        (const.SUCCESS, messages.DevicePowerState.name, ["1", "0"]),
    )
    await device.connect()
    assert device.is_server_only
    assert not emulator.requests_named(messages.GetUiState.name)
    identity = await device.read_identity()
    assert identity.movie_zones == 0


@pytest.mark.asyncio
async def test_discovery_rejects_long_or_duplicate_serials(
    device: Device, emulator: Emulator
):
    await device.connect()
    emulator.register_mock_command(
        ("01",),
        messages.GetAvailableDevicesBySerialNumber.name,
        (
            const.SUCCESS,
            messages.AvailableDevicesBySerialNumber.name,
            ["00000000123A", "000000000123A"],
        ),
    )
    with pytest.raises(KaleidescapeError, match="Duplicate"):
        await device.discover_devices()

    emulator.register_mock_command(
        ("01",),
        messages.GetAvailableDevicesBySerialNumber.name,
        (
            const.SUCCESS,
            messages.AvailableDevicesBySerialNumber.name,
            ["00000000123A", "100000000123A"],
        ),
    )
    with pytest.raises(ValueError, match="serial"):
        await device.discover_devices()


@pytest.mark.asyncio
async def test_unknown_readiness_is_a_typed_protocol_failure(
    device: Device, emulator: Emulator
):
    await device.connect()
    emulator.register_mock_command(
        ("01",),
        messages.GetSystemReadinessState.name,
        (const.SUCCESS, messages.SystemReadinessState.name, ["99"]),
    )
    with pytest.raises(KaleidescapeError, match="Unknown protocol state"):
        await device.read_control_state()


@pytest.mark.asyncio
async def test_explicit_pause_and_back_are_distinct(device: Device, emulator: Emulator):
    await device.connect()
    await device.pause_on()
    await device.pause_on()
    await device.pause_off()
    await device.pause_off()
    await device.back()
    assert len(emulator.requests_named(messages.PauseOn.name)) == 2
    assert len(emulator.requests_named(messages.PauseOff.name)) == 2
    assert len(emulator.requests_named(messages.Back.name)) == 1
    assert not emulator.requests_named(messages.Pause.name)
    assert not emulator.requests_named(messages.Cancel.name)


HANDLE = "26-0.0-S_c446c8e2"
SERIAL = "00000000123A"


@pytest.mark.asyncio
async def test_prepare_is_read_only_and_send_once(device: Device, emulator: Emulator):
    await device.connect()
    prepared = await device.prepare_movie_launch(HANDLE, serial=SERIAL)
    assert prepared.handle == HANDLE
    assert prepared.title == "Turtle Odyssey"
    assert prepared.bookmark_and_options == "26-0.NUL;158-0=1"
    assert not emulator.requests_named(messages.PlayMovieExperimental.name)
    with pytest.raises(ValueError, match="opt-in"):
        await device.send_movie_launch(prepared)
    assert not emulator.requests_named(messages.PlayMovieExperimental.name)

    acknowledgement = await device.send_movie_launch(prepared, allow_experimental=True)
    assert acknowledgement.status == const.SUCCESS
    sent = emulator.requests_named(messages.PlayMovieExperimental.name)
    assert len(sent) == 1
    assert sent[0].device_id == "#" + SERIAL
    assert sent[0].fields == [HANDLE, "26-0.NUL;158-0=1", ""]


@pytest.mark.asyncio
async def test_rejects_missing_movie_metadata(device: Device, emulator: Emulator):
    await device.connect()
    emulator.register_mock_command(
        ("#" + SERIAL,),
        messages.GetContentDetails.name,
        [
            (
                const.SUCCESS,
                messages.ContentDetailsOverview.name,
                ["1", HANDLE, "movies"],
            ),
            (const.SUCCESS, messages.ContentDetails.name, ["1", "Year", "2020"]),
        ],
    )
    with pytest.raises(KaleidescapeError, match="no movie title"):
        await device.prepare_movie_launch(HANDLE, serial=SERIAL)
    assert not emulator.requests_named(messages.PlayMovieExperimental.name)


@pytest.mark.asyncio
async def test_rejects_stale_preparation(device: Device, emulator: Emulator):
    await device.connect()
    prepared = await device.prepare_movie_launch(HANDLE, serial=SERIAL)
    expired = replace(prepared, prepared_at=prepared.prepared_at - 11)
    with pytest.raises(KaleidescapeError, match="expired"):
        await device.send_movie_launch(expired, allow_experimental=True)
    assert not emulator.requests_named(messages.PlayMovieExperimental.name)


@pytest.mark.parametrize(
    "profile",
    [
        MovieLaunchProfile(bookmark="Other.NUL"),
        MovieLaunchProfile(options=(("158-0", "1"), ("158-0", "2"))),
        MovieLaunchProfile(options=(("bad:key", "1"),)),
    ],
)
def test_invalid_profiles_fail_before_send(profile: MovieLaunchProfile):
    with pytest.raises(ValueError):
        profile.field("26-0")
