"""Tests for message module."""

# pylint: disable=missing-function-docstring,protected-access

import pytest

from kaleidescape import const
from kaleidescape import message as messages
from kaleidescape.error import KaleidescapeError, MessageParseError
from kaleidescape.message import (
    MESSAGE_TYPE_REQUEST,
    GetContentDetails,
    MessageParser,
    Request,
    Response,
    UserDefinedEvent,
)


def test_message_parser():
    """Test message parser."""
    parsed = MessageParser("01/2/003:MESSAGE_NAME:/123")
    assert parsed.device_id == "01"
    assert parsed.zone == 0
    assert parsed.seq == 2
    assert parsed.status == 3
    assert parsed.name == "MESSAGE_NAME"
    assert len(parsed.fields) == 0
    assert parsed.checksum == 123


def test_message_parser_with_fields():
    """Test message parser with fields."""
    parsed = MessageParser("01/2/003:MESSAGE_NAME:field1:field2:/123")
    assert parsed.device_id == "01"
    assert parsed.zone == 0
    assert parsed.seq == 2
    assert parsed.status == 3
    assert parsed.name == "MESSAGE_NAME"
    assert parsed.fields == ["field1", "field2"]
    assert parsed.checksum == 123


def test_message_parser_event():
    """Test message parse as event."""
    parsed = MessageParser("01/!/003:MESSAGE_NAME:field1:field2:/123")
    assert parsed.device_id == "01"
    assert parsed.zone == 0
    assert parsed.seq == -1
    assert parsed.status == 3
    assert parsed.name == "MESSAGE_NAME"
    assert parsed.fields == ["field1", "field2"]
    assert parsed.checksum == 123


def test_message_parser_request():
    """Test message parse when message is request."""
    parsed = MessageParser("01/1/GET_MESSAGE_NAME:field1:field2:/123", True)
    assert parsed.device_id == "01"
    assert parsed.zone == 0
    assert parsed.seq == 1
    assert parsed.status == 0
    assert parsed.name == "GET_MESSAGE_NAME"
    assert parsed.fields == ["field1", "field2"]
    assert parsed.checksum == 0


def test_message_parser_device_id():
    """Test message parser device_id."""
    parsed = MessageParser("01/2/003:MESSAGE_NAME:/123")
    assert parsed.device_id == "01"
    assert parsed.zone == 0
    parsed = MessageParser("01.02/2/003:MESSAGE_NAME:/123")
    assert parsed.device_id == "01"
    assert parsed.zone == 2
    parsed = MessageParser("#0A123F12/2/003:MESSAGE_NAME:/123")
    assert parsed.device_id == "#0A123F12"
    assert parsed.zone == 0
    parsed = MessageParser("#0A123F12.02/2/003:MESSAGE_NAME:/123")
    assert parsed.device_id == "#0A123F12"
    assert parsed.zone == 2


@pytest.mark.parametrize(
    ("message", "code"),
    [
        # Device id
        ("1/2/003:MESSAGE_NAME:/123", const.ERROR_INVALID_DEVICE),
        ("001/2/003:MESSAGE_NAME:/123", const.ERROR_INVALID_DEVICE),
        ("aa/2/003:MESSAGE_NAME:/123", const.ERROR_INVALID_DEVICE),
        ("/2/003:MESSAGE_NAME:/123", const.ERROR_INVALID_DEVICE),
        ("01./2/003:MESSAGE_NAME:/123", const.ERROR_INVALID_DEVICE),
        ("01.0a/2/003:MESSAGE_NAME:/123", const.ERROR_INVALID_DEVICE),
        # Sequence number
        ("01//003:MESSAGE_NAME:/123", const.ERROR_INVALID_SEQ_NUMBER),
        ("01/22/003:MESSAGE_NAME:/123", const.ERROR_INVALID_SEQ_NUMBER),
        ("01/aa/003:MESSAGE_NAME:/123", const.ERROR_INVALID_SEQ_NUMBER),
        # Status
        ("01/2/0001:MESSAGE_NAME:/123", const.ERROR_UNDETERMINED_ERROR),
        ("01/2/01:MESSAGE_NAME:/123", const.ERROR_UNDETERMINED_ERROR),
        ("01/2/aaa:MESSAGE_NAME:/123", const.ERROR_UNDETERMINED_ERROR),
        ("01/2/:MESSAGE_NAME:/123", const.ERROR_UNDETERMINED_ERROR),
        # Name
        ("01/2/003:MESSAGE_NAME/123", const.ERROR_INVALID_REQUEST),
        ("01/2/003::/123", const.ERROR_INVALID_REQUEST),
        ("01/2/003:", const.ERROR_INVALID_REQUEST),
        # Fields
        ("01/2/000:MESSAGE_NAME:field/123", const.ERROR_INVALID_PARAMETER),
        ("01/2/000:MESSAGE_NAME:field", const.ERROR_INVALID_PARAMETER),
        # Checksum
        ("01/2/000:MESSAGE_NAME:/abc", const.ERROR_CHECKSUM_ERROR),
        ("01/2/000:MESSAGE_NAME:/", const.ERROR_CHECKSUM_ERROR),
    ],
)
def test_message_parser_errors(message: str, code: int):
    """Test each malformed message part is rejected with its specific error code."""
    with pytest.raises(MessageParseError) as err:
        MessageParser(message)
    assert err.value.code == code
    assert const.RESPONSE_ERROR[code] in str(err.value)


def test_message_parser_seq():
    """Test message parser seq."""
    parsed = MessageParser("01/9/003:MESSAGE_NAME:/123")
    assert parsed.seq == 9
    parsed = MessageParser("01/!/003:MESSAGE_NAME:/123")
    assert parsed.seq == -1


def test_message_parser_status():
    """Test message parser status."""
    parsed = MessageParser("01/0/999:MESSAGE_NAME:/123")
    assert parsed.status == 999


def test_message_parser_name():
    """Test message parser name."""
    parsed = MessageParser("01/2/003:MESSAGE_NAME_123:/123")
    assert parsed.name == "MESSAGE_NAME_123"


def test_message_parser_field_decoding():
    """Test message parser field decoding."""
    parsed = MessageParser("01/2/003:MESSAGE_NAME:f\\d225ncy:/123")
    assert parsed.fields == ["fáncy"]
    parsed = MessageParser("01/2/003:MESSAGE_NAME:f\\\\d225ncy:/123")
    assert parsed.fields == ["f\\d225ncy"]
    parsed = MessageParser("01/2/003:MESSAGE_NAME:colon\\:separated:/123")
    assert parsed.fields == ["colon:separated"]
    parsed = MessageParser("01/2/003:MESSAGE_NAME:forward\\/slash:/123")
    assert parsed.fields == ["forward/slash"]
    parsed = MessageParser("01/2/003:MESSAGE_NAME:back\\\\slash:/123")
    assert parsed.fields == ["back\\slash"]
    parsed = MessageParser("01/2/003:MESSAGE_NAME:new\\nline:/123")
    assert parsed.fields == ["new\nline"]
    parsed = MessageParser("01/2/003:MESSAGE_NAME:new\\\nline:/123")
    assert parsed.fields == ["new\nline"]
    parsed = MessageParser("01/2/003:MESSAGE_NAME:new\\\rline:/123")
    assert parsed.fields == ["new\rline"]
    parsed = MessageParser("01/2/003:MESSAGE_NAME:tab\\tbed:car\\rriage:/123")
    assert parsed.fields == ["tab\tbed", "car\rriage"]


def test_message_parser_empty_name():
    """Test message parser with empty name."""
    parsed = MessageParser("01/2/000:/123")
    assert parsed.device_id == "01"
    assert parsed.zone == 0
    assert parsed.seq == 2
    assert parsed.status == 0
    assert parsed.name == ""
    assert len(parsed.fields) == 0
    assert parsed.checksum == 123


def test_message_response_with_empty_name():
    """Test message response with empty name"""
    message = Response(MessageParser("01/2/999:/123"))
    assert message.device_id == "01"
    assert message.zone == 0
    assert message.seq == 2
    assert message.status == 999
    assert message.fields == []
    assert message.is_event is False
    assert message.is_error is True
    assert message.error == const.RESPONSE_ERROR[message.status]
    assert str(message) == "01/2/999:/123"


def test_message_response_from_command():
    """Test message response from command."""
    message = Response.factory("01/2/000:AVAILABLE_DEVICES:01:02:/123")
    assert isinstance(message, messages.AvailableDevices)
    assert message.type == messages.MESSAGE_TYPE_RESPONSE
    assert message.device_id == "01"
    assert message.zone == 0
    assert message.seq == 2
    assert message.status == 0
    assert message.name == "AVAILABLE_DEVICES"
    assert message.fields == ["01", "02"]
    assert message.is_event is False
    assert message.is_error is False
    assert str(message) == "01/2/000:AVAILABLE_DEVICES:01:02:/123"


def test_message_response_from_event():
    """Test message response from event."""
    message = Response.factory("01/!/000:AVAILABLE_DEVICES:01:02:/123")
    assert isinstance(message, messages.AvailableDevices)
    assert message.type == messages.MESSAGE_TYPE_EVENT
    assert message.device_id == "01"
    assert message.zone == 0
    assert message.seq == -1
    assert message.status == 0
    assert message.name == "AVAILABLE_DEVICES"
    assert message.fields == ["01", "02"]
    assert message.is_event is True
    assert message.is_error is False
    assert str(message) == "01/!/000:AVAILABLE_DEVICES:01:02:/123"


def test_message_response_with_error_status():
    """Test message response with error status."""
    message = Response.factory("01/2/003:AVAILABLE_DEVICES:01:02:/123")
    assert isinstance(message, messages.AvailableDevices)
    assert message.type == messages.MESSAGE_TYPE_RESPONSE
    assert message.device_id == "01"
    assert message.zone == 0
    assert message.seq == 2
    assert message.status == 3
    assert message.name == "AVAILABLE_DEVICES"
    assert message.fields == ["01", "02"]
    assert message.is_event is False
    assert message.is_error is True
    assert message.error == const.RESPONSE_ERROR[message.status]
    assert str(message) == "01/2/003:AVAILABLE_DEVICES:01:02:/123"


def test_message_response_with_parse_error():
    """Test message response with parse error."""
    with pytest.raises(MessageParseError) as err:
        Response.factory("ERR/2/003:AVAILABLE_DEVICES:01:02:/123")
    assert const.RESPONSE_ERROR[const.ERROR_INVALID_DEVICE] in str(err.value)


def test_message_request():
    """Test message request."""
    message = Request()
    assert message.device_id == "01"
    assert message.zone == 0
    assert message.seq == -1
    assert message.status == 0
    assert message.fields == []
    assert message.type == MESSAGE_TYPE_REQUEST


def test_message_request_with_fields():
    """Test message request."""
    message = Request(0, ["field1", "field2"])
    assert message.device_id == "01"
    assert message.zone == 0
    assert message.seq == -1
    assert message.status == 0
    assert message.fields == ["field1", "field2"]
    assert message.type == MESSAGE_TYPE_REQUEST


def test_message_get_available_devices():
    """Test message request as GET_AVAILABLE_DEVICES."""
    message = messages.GetAvailableDevices()
    message.seq = 2
    assert message.device_id == "01"
    assert message.zone == 0
    assert message.seq == 2
    assert message.status == 0
    assert message.name == "GET_AVAILABLE_DEVICES"
    assert message.fields == []
    assert str(message) == "01/2/GET_AVAILABLE_DEVICES:"


def test_user_defined_event_parsing():
    """Test message response with USER_DEFINED_EVENT."""
    resp = Response.factory("01/!/000:USER_DEFINED_EVENT:VOLUME_UP:/123")
    assert isinstance(resp, UserDefinedEvent)
    assert resp.name == "USER_DEFINED_EVENT"
    assert resp.fields == ["VOLUME_UP"]

    resp = Response.factory("01/!/000:USER_DEFINED_EVENT:SET_VOLUME_LEVEL=28:/123")
    assert isinstance(resp, UserDefinedEvent)
    assert resp.name == "USER_DEFINED_EVENT"
    assert resp.fields == ["SET_VOLUME_LEVEL", "28"]


class ExternalRequest(Request):
    """Represent an existing third-party Request subclass."""

    name = "EXTERNAL_REQUEST"


def test_field_encoding_and_reuse():
    request = ExternalRequest(fields=["a:b/c\\d", "á\n\r\t"])
    assert str(request) == r"01/!/EXTERNAL_REQUEST:a\:b\/c\\d:\d225\n\r\t:"
    request.seq = 3
    assert str(request).startswith("01/3/")
    request.fields[0] = "new"
    assert str(request).startswith("01/3/EXTERNAL_REQUEST:new:")


def test_zone_and_serial_are_encoded_without_losing_identity():
    request = ExternalRequest(2, ["movie"], device_id="#00000000123a")
    request.seq = 1
    assert str(request) == "#00000000123A.02/1/EXTERNAL_REQUEST:movie:"
    assert MessageParser(str(request), True).device_id == "#00000000123A"
    assert MessageParser(str(request), True).zone == 2


@pytest.mark.parametrize("value", ["bad/name", "#123456789ABCD", "", "??"])
def test_invalid_target_is_rejected(value):
    with pytest.raises(ValueError, match="device identifier"):
        ExternalRequest(device_id=value)


@pytest.mark.parametrize("value", ["é🎬", "\x00", "\x1f"])
def test_unsupported_field_fails_before_transmission(value):
    with pytest.raises(ValueError, match="unsupported character"):
        str(ExternalRequest(fields=[value]))


def test_unknown_sequence_is_not_event():
    response = Response.factory("01/?/000:AVAILABLE_DEVICES:01:/123")
    assert response.seq == -2
    assert not response.is_event


@pytest.mark.parametrize(
    "wire", ["01/0/000:TEST:bad\\d2:/123", "01/0/000:TEST:bad\\q:/123"]
)
def test_malformed_escaping_has_protocol_error(wire):
    with pytest.raises(MessageParseError) as error:
        MessageParser(wire)
    assert error.value.code == const.ERROR_INVALID_PARAMETER


def content_reply(name: str, *fields: str) -> Response:
    """Make a response without using the production request serializer."""
    body = ":".join(fields)
    return Response.factory(f"01/0/000:{name}:{body + ':' if body else ''}/1")


class ContentDetailResponses:
    """One-response transport that also delivers a complete multipart group."""

    timeout = 0.01

    def __init__(self, *items: Response):
        self.items = items

    async def send(self, request):
        for item in self.items:
            request.set(item)
        return self.items[0]

    def clear(self, request):
        pass

    async def retire(self):
        """Stand in for retiring the malformed reply stream."""


@pytest.mark.asyncio
async def test_zero_rows_are_a_valid_complete_overview():
    request = GetContentDetails(fields=["26-0.0-S_test", ""])
    rows = await request.send(
        ContentDetailResponses(
            content_reply("CONTENT_DETAILS_OVERVIEW", "0", "26-0.0-S_test", "movies")
        )
    )
    assert len(rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "items",
    [
        (content_reply("OTHER"),),
        (
            content_reply("CONTENT_DETAILS_OVERVIEW", "1", "other", "movies"),
            content_reply("CONTENT_DETAILS", "1", "Title", "A"),
        ),
        (
            content_reply("CONTENT_DETAILS_OVERVIEW", "1", "26-0.0-S_test", "movies"),
            content_reply("OTHER", "1", "Title", "A"),
        ),
        (
            content_reply("CONTENT_DETAILS_OVERVIEW", "2", "26-0.0-S_test", "movies"),
            content_reply("CONTENT_DETAILS", "1", "Title", "A"),
            content_reply("CONTENT_DETAILS", "1", "Year", "2026"),
        ),
        (
            content_reply("CONTENT_DETAILS_OVERVIEW", "2", "26-0.0-S_test", "movies"),
            content_reply("CONTENT_DETAILS", "1", "Title", "A"),
            content_reply("CONTENT_DETAILS", "2", "Title", "B"),
        ),
        (content_reply("CONTENT_DETAILS_OVERVIEW", "1001", "26-0.0-S_test", "movies"),),
    ],
)
async def test_invalid_or_mismatched_group_is_rejected(items):
    request = GetContentDetails(fields=["26-0.0-S_test", ""])
    with pytest.raises(KaleidescapeError):
        await request.send(ContentDetailResponses(*items))
