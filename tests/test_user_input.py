"""Coverage for user input commands and events."""

from unittest.mock import AsyncMock

import pytest

from kaleidescape import message as messages
from kaleidescape.device import Device


def test_user_input_response():
    """Parse the current prompt and entry from a response or event."""
    response = messages.Response.factory(
        "01/!/000:USER_INPUT:01:Search:Hi\\: there:/00"
    )
    assert isinstance(response, messages.UserInput)
    assert (response.field_type, response.field_prompt, response.field_entry) == (
        1,
        "Search",
        "Hi: there",
    )


def test_text_commands_escape_delimiters():
    """Text fields retain protocol delimiters through request encoding."""
    request = messages.SetUserInputEntry(fields=[r"A:/\\B"])
    parsed = messages.MessageParser(str(request), True)
    assert parsed.fields == [r"A:/\\B"]


@pytest.mark.asyncio
async def test_user_input_device_commands():
    """Forward reads, input and editing through the existing request path."""
    device = Device("127.0.0.1")
    response = messages.Response.factory("01/1/000:USER_INPUT:01:Search::/00")
    device._send = AsyncMock(return_value=response)
    assert await device.get_user_input() is response
    await device.keyboard_literal(":")
    device._send.assert_awaited_with(messages.KeyboardLiteral, 0, [":"])
    await device.set_user_input_entry("Example")
    device._send.assert_awaited_with(messages.SetUserInputEntry, 0, ["Example"])
    await device.backspace()
    device._send.assert_awaited_with(messages.Backspace)
    await device.go_movie_collection("Watch Soon")
    device._send.assert_awaited_with(messages.GoMovieCollection, 0, ["Watch Soon"])
    await device.go_movie_store()
    device._send.assert_awaited_with(messages.GoMovieStore)
    assert (
        str(messages.GoMovieCollection(fields=["A:B"]))
        == r"01/!/GO_MOVIE_COLLECTION:A\:B:"
    )
    assert str(messages.GoMovieStore()) == "01/!/GO_MOVIE_STORE:"
    await device.go_screen_saver()
    device._send.assert_awaited_with(messages.GoScreenSaver)
    await device.stop_screen_saver()
    device._send.assert_awaited_with(messages.StopScreenSaver)
    assert str(messages.StopScreenSaver()) == "01/!/STOP_SCREEN_SAVER:"
    for value in ("", "ab", "\n", "é"):
        with pytest.raises(ValueError):
            await device.keyboard_literal(value)


@pytest.mark.asyncio
async def test_user_input_event_is_dispatched():
    """Input changes reach existing dispatcher subscribers."""
    device = Device("127.0.0.1")
    seen = []
    device.dispatcher.connect(lambda *args: seen.append(args))
    event = messages.Response.factory("01/!/000:USER_INPUT:01:Search:Hi:/00")
    await device._handle_event(event)
    assert seen == [("USER_INPUT", ["01", "Search", "Hi"])]
