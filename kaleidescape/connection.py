"""Class handling network connection to hardware device."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, cast

from . import const
from .error import (
    MessageParseError,
    RequestNotSentError,
    RequestOutcomeUnknownError,
    format_error,
)
from .message import Response

if TYPE_CHECKING:
    from .dispatcher import Dispatcher
    from .message import Request

_LOGGER = logging.getLogger(__name__)

SEPARATOR = "\n"
SEPARATOR_BYTES = SEPARATOR.encode("latin-1")


class Connection:
    """Class handling network connection to hardware device."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        on_event: Callable[[Response], Coroutine[object, object, object]] | None = None,
        on_reconnect: Callable[[], Coroutine[object, object, object]] | None = None,
    ) -> None:
        """Initializes connection."""
        self._dispatcher = dispatcher
        self._on_event: (
            Callable[[Response], Coroutine[object, object, object]] | None
        ) = on_event
        self._on_reconnect: Callable[[], Coroutine[object, object, object]] | None = (
            on_reconnect
        )

        self._ip: str | None = None
        self._port: int | None = None
        self._timeout: float | None = None
        self._state: str = const.STATE_DISCONNECTED
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._response_handler_task: asyncio.Task | None = None
        self._event_tasks: set[asyncio.Task] = set()
        self._reconnect_delay: float | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_enabled: bool = False
        self._pending_requests: dict[int, Request] = {}
        self._next_seq: int = 0
        self._retiring = False

    @property
    def dispatcher(self) -> Dispatcher:
        """Return dispatcher instance."""
        return self._dispatcher

    @property
    def ip(self) -> str | None:
        """Return ip of the server connected to."""
        return self._ip

    @property
    def port(self) -> int | None:
        """Return port of the hardware device to connect to."""
        return self._port

    @property
    def timeout(self) -> float | None:
        """Return connection timeout in seconds."""
        return self._timeout

    @property
    def state(self) -> str:
        """Return state of the connection to the hardware device."""
        return self._state

    async def connect(
        self,
        ip: str,
        port: int,
        timeout: float,  # noqa: ASYNC109 - stored as the per-request timeout
        reconnect: bool = False,
        reconnect_delay: float = const.DEFAULT_RECONNECT_DELAY,
    ) -> None:
        """Connect to the hardware device."""
        if self._state == const.STATE_CONNECTED:
            return

        self._ip = ip
        self._port = port
        self._timeout = timeout

        # Disable auto_reconnect until a good connect
        self._reconnect_enabled = False

        await self._connect()

        self._reconnect_enabled = reconnect
        self._reconnect_delay = reconnect_delay

        _LOGGER.info("Connected to %s", self._ip)

    async def _connect(self) -> None:
        """Connect to server."""
        try:
            connection = asyncio.open_connection(self._ip, self._port)
            self._reader, self._writer = await asyncio.wait_for(
                connection, self._timeout
            )
        except ConnectionError:
            # Don't allow subclasses of ConnectionError to be cast as OSErrors below
            raise
        except (TimeoutError, OSError) as err:
            # Generalize connection errors
            raise ConnectionError(format_error(err)) from err

        self._response_handler_task = asyncio.create_task(self._response_handler())

        self._state = const.STATE_CONNECTED
        self._retiring = False

    async def _response_handler(self) -> None:
        """Main loop receiving responses and events from hardware device."""
        assert self._reader

        while True:
            try:
                result = await self._reader.readuntil()

                response = Response.factory(result.decode("latin-1").strip())
                _LOGGER.debug("Response received '%s'", response.message)

                if response.is_event:
                    # Events are unsolicited notifications about a change in state.
                    if self._on_event:
                        task = asyncio.create_task(self._deliver_event(response))
                        self._event_tasks.add(task)
                        task.add_done_callback(self._event_tasks.discard)
                elif response.seq >= 0:
                    if response.seq not in self._pending_requests:
                        # Usually a late reply to a request that already timed out.
                        _LOGGER.warning(
                            "Ignoring response with no pending request '%s'", response
                        )
                    else:
                        request = self._pending_requests[response.seq]
                        if response.device_id.upper() != request.device_id.upper() or (
                            not request._responses  # pylint: disable=protected-access
                            and not response.is_error
                            and request.expected_response_name is not None
                            and response.name != request.expected_response_name
                        ):
                            request.fail(
                                RequestOutcomeUnknownError("Unrelated protocol reply")
                            )
                            asyncio.create_task(
                                self._handle_connection_error(
                                    RequestOutcomeUnknownError(
                                        "Unrelated protocol reply"
                                    )
                                )
                            )
                            return
                        request.set(response)
            except (asyncio.IncompleteReadError, OSError) as err:
                asyncio.create_task(self._handle_connection_error(err))
                return
            except MessageParseError:
                _LOGGER.exception("Failed to parse response")
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.exception("Unhandled exception in response handler")
                asyncio.create_task(self._handle_connection_error(err))
                return

    async def _deliver_event(self, response: Response) -> None:
        """Isolate a failed event callback from the protocol reader."""
        assert self._on_event is not None
        try:
            await self._on_event(response)
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-except
            _LOGGER.warning("Event callback failed: %s", type(error).__name__)

    async def _handle_connection_error(self, err: Exception):
        """Handle connection failures and schedule reconnect."""
        if self._retiring or self._state != const.STATE_CONNECTED:
            return
        self._retiring = True

        await self._disconnect()

        if self._reconnect_enabled:
            self._state = const.STATE_RECONNECTING
            self._reconnect_task = asyncio.create_task(self._reconnect())
        else:
            self._state = const.STATE_DISCONNECTED

        _LOGGER.error(
            "Disconnected from %s %s('%s')", self._ip, type(err).__name__, err
        )
        self._dispatcher.send(const.STATE_DISCONNECTED)

    async def _reconnect(self):
        """Reconnect to server."""
        assert self._reconnect_delay
        try:
            while self._state != const.STATE_CONNECTED:
                try:
                    await self._connect()
                except ConnectionError as err:
                    _LOGGER.warning("Failed reconnect to %s with '%s'", self._ip, err)
                    await self._disconnect()
                    await asyncio.sleep(self._reconnect_delay)
                else:
                    if self._on_reconnect:
                        try:
                            await self._on_reconnect()
                        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-except
                            _LOGGER.warning(
                                "Post-reconnect synchronization failed: %s",
                                type(error).__name__,
                            )
                            await self._disconnect()
                            self._state = const.STATE_RECONNECTING
                            await asyncio.sleep(self._reconnect_delay)
                            continue
                    self._reconnect_task = None
                    self._dispatcher.send(const.STATE_CONNECTED)
                    _LOGGER.info("Reconnected to %s", self._ip)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unhandled exception in reconnect")
            raise

    async def disconnect(self):
        """Disconnect from server."""
        if self._state == const.STATE_DISCONNECTED:
            return

        # Cancel pending reconnect task
        if self._reconnect_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(BaseException):
                await self._reconnect_task
            self._reconnect_task = None

        await self._disconnect()
        self._state = const.STATE_DISCONNECTED

        _LOGGER.info("Disconnected from %s", self._ip)
        self._dispatcher.send(const.STATE_DISCONNECTED)

    async def _disconnect(self):
        """Disconnect from server."""
        for request in self._pending_requests.values():
            request.fail(
                RequestOutcomeUnknownError("Connection closed before acknowledgement")
            )
        tasks = [
            task for task in self._event_tasks if task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._event_tasks.clear()
        if self._response_handler_task:
            self._response_handler_task.cancel()
            with contextlib.suppress(BaseException):
                await self._response_handler_task
            self._response_handler_task = None

        if self._writer:
            self._writer.close()
            self._writer = None

        self._reader = None
        self._pending_requests.clear()

    async def retire(self) -> None:
        """Retire an ambiguous response stream without replaying a request."""
        if self._state == const.STATE_CONNECTED:
            await self._handle_connection_error(
                RequestOutcomeUnknownError("Response stream retired")
            )

    async def send(self, request: Request) -> Response:
        """Send one request within a single admission/write/ack deadline."""
        if self._state != const.STATE_CONNECTED:
            err = "Not connected to device"
            _LOGGER.error(err)
            raise RequestNotSentError(err)

        assert self._timeout is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout

        while request.seq < 0:
            try:
                # Devices can only handle 10 concurrent requests. Numbers are handed
                # out in rotation so one freed by a timed-out request is reused last;
                # the device's late reply to it would otherwise reach the next request.
                request.seq = next(
                    seq
                    for i in range(10)
                    if (seq := (self._next_seq + i) % 10) not in self._pending_requests
                )
                self._next_seq = (request.seq + 1) % 10
            except StopIteration as error:
                remaining = deadline - loop.time()
                if remaining <= 0 or self._state != const.STATE_CONNECTED:
                    raise RequestNotSentError("Request admission timed out") from error
                await asyncio.sleep(min(0.01, remaining))

        self._pending_requests[request.seq] = request
        request._deadline = deadline  # pylint: disable=protected-access

        try:
            wire = str(request).encode("latin-1") + SEPARATOR_BYTES
            assert self._writer
            writer = self._writer
            if deadline - loop.time() <= 0:
                raise RequestNotSentError("Request deadline expired before write")
        except (OSError, ValueError, UnicodeError, TypeError) as error:
            raise RequestNotSentError("Request could not be encoded") from error
        try:
            writer.write(wire)
            async with asyncio.timeout_at(deadline):
                await writer.drain()
                _LOGGER.debug("Request sent: %s", request.name)
                response = await request.wait()
        except (
            TimeoutError,
            OSError,
            ConnectionError,
            asyncio.CancelledError,
        ) as error:
            # After writer.write the device may have acted. Retire the stream so
            # a late reply cannot acknowledge a later command on a reused slot.
            await self._handle_connection_error(
                RequestOutcomeUnknownError("Request outcome is unconfirmed")
            )
            if isinstance(error, asyncio.CancelledError):
                raise
            raise RequestOutcomeUnknownError(
                "Request outcome is unconfirmed"
            ) from error

        return response

    def clear(self, request: Request):
        """Release the request's sequence number so another request can use it."""
        # The seq may already belong to a newer request after a disconnect cleared
        # the pending list.
        if self._pending_requests.get(request.seq) is request:
            del self._pending_requests[request.seq]

    @staticmethod
    async def resolve(host: str) -> str:
        """Resolve hostname to ip address."""
        try:
            addrinfo = await asyncio.get_running_loop().getaddrinfo(
                host,
                None,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
            if len(addrinfo) < 1:
                raise ConnectionError("Unexpected zero length addresses response")
        except OSError as err:
            raise ConnectionError(f"Failed to resolve host {host}") from err

        return cast(str, addrinfo[0][4][0])
