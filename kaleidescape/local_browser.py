"""Local server browser access through built-in or injected transport.

The built-in transport connects directly to a literal private IP and bounds each
request. Caller-owned transports may be injected for specialized network policy.
Constructing either browser object performs no I/O.
"""

# pylint: disable=missing-function-docstring,too-many-boolean-expressions

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import math
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

from .error import KaleidescapeError
from .library_parser import (
    LibraryFormatError,
    Tree,
    artwork_path,
    catalog,
    clean,
    detail_collections,
    details,
    identity,
)

MAX_PAGE = 8 * 1024 * 1024
MAX_ARTWORK = 8 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
TEXT_TYPES = {"text/html", "application/xhtml+xml"}
CHARSETS = {"utf-8", "utf8", "iso-8859-1", "latin-1", "windows-1252"}
RESERVED_COLLECTIONS = {"all", "new", "played", "paused", "scenes", "trash", "deleted"}
REMOVAL_ACTIONS = {
    "favorites": ("Favorites", "remove_from_favorites", "Remove-Often"),
    "watchsoon": ("WatchSoon", "remove_from_watch_soon", "Remove-Soon"),
    "new": ("New", "remove_from_new", "Remove-New"),
    "played": ("Played", "remove_from_played", "Remove-Played"),
    "paused": ("Paused", "remove_from_paused", "Remove-Paused"),
}


class BrowserResponseError(KaleidescapeError):
    """A browser response was too large, malformed, or outside the allowed routes."""


class BrowserOutcomeUnknownError(KaleidescapeError):
    """A collection POST may have reached the browser, but its result is unknown."""


@dataclass(frozen=True)
class BrowserResponse:
    """A bounded response provided by an owned HTTP transport."""

    status: int
    content_type: str
    body: bytes
    charset: str | None = None
    content_encoding: str = "identity"
    location: str | None = None


class BrowserTransport(Protocol):
    """Enforce the network policy above and encode POST fields as multipart/form-data.

    Never retry a POST, and return only after the full bounded response is read.
    """

    async def get(self, path: str) -> BrowserResponse: ...

    async def post(self, path: str, fields: dict[str, str]) -> BrowserResponse: ...


@dataclass(frozen=True)
class CollectionPostResult:
    """HTTP acknowledgement only; membership must be checked separately."""

    status: int
    location: str | None


def _text(response: BrowserResponse) -> str:
    if (
        response.status != 200
        or response.content_type.lower() not in TEXT_TYPES
        or response.content_encoding.lower() != "identity"
        or len(response.body) > MAX_PAGE
    ):
        raise BrowserResponseError("Unsupported browser page response")
    charset = (response.charset or "iso-8859-1").lower()
    if charset not in CHARSETS:
        raise BrowserResponseError("Unsupported browser page charset")
    try:
        return response.body.decode(charset)
    except UnicodeError as error:
        raise BrowserResponseError("Invalid browser page encoding") from error


def _image_type(body: bytes) -> str | None:
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def _collection_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or value != " ".join(value.split())
        or value.casefold() in RESERVED_COLLECTIONS
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Invalid collection name")
    return value


def form_targets(page: str, handle: str) -> dict[str, str]:
    """Parse the live add-only form and selected version's checkbox."""
    identity(handle)
    root = Tree(page).root
    form = root.find(id="main", tag="form")
    if (
        form is None
        or form.attrs.get("method", "").lower() != "post"
        or form.attrs.get("enctype", "").lower() != "multipart/form-data"
        or form.attrs.get("action", "") not in {"", "/movies", "movies"}
    ):
        raise LibraryFormatError("Collection add form was not recognized")
    button = form.find(id="add_to_collection", tag="button")
    field = form.find(id="add_to_collection_select_dropdown_value", tag="input")
    dropdown = form.find(id="add_to_collection_select_dropdown")
    if (
        button is None
        or button.attrs.get("onclick", "").strip()
        != "submit_parent_form_once( event, 'Add-To-Collection' );"
        or field is None
        or field.attrs.get("name") != "add_to_collection_select"
        or dropdown is None
    ):
        raise LibraryFormatError("Collection add form was not recognized")
    controls = [
        node
        for node in form.nodes()
        if node.tag == "input"
        and node.attrs.get("name") == "check-" + handle
        and node.attrs.get("type", "").lower() == "checkbox"
        and "disabled" not in node.attrs
    ]
    if not controls or any(node.attrs.get("value", "on") != "on" for node in controls):
        raise LibraryFormatError("Selected version cannot be added")
    targets: dict[str, str] = {}
    for node in dropdown.nodes():
        if (
            node.tag != "li"
            or "dropdown_option" not in node.attrs.get("class", "").split()
        ):
            continue
        label = clean(node.text(), 256)
        if not label or label.casefold() in RESERVED_COLLECTIONS:
            continue
        value = node.find(tag="input")
        if value is None or "disabled" in value.attrs:
            continue
        raw = value.attrs.get("value", "")
        if not raw:
            continue
        if label in targets or len(raw) > 512 or any(ord(char) < 32 for char in raw):
            raise LibraryFormatError("Invalid collection choice")
        targets[label] = raw
    if len(targets) > 200:
        raise LibraryFormatError("Too many collection choices")
    return targets


def removal_form(page: str, handle: str, collection: str) -> tuple[str, str]:
    """Verify the exact built-in collection form and selected checkbox."""
    identity(handle)
    if not isinstance(collection, str):
        raise TypeError("Collection name must be text")
    key = re.sub(r"[\s_-]+", "", collection.casefold())
    if key not in REMOVAL_ACTIONS:
        raise ValueError("Unsupported collection removal")
    name, button_id, action = REMOVAL_ACTIONS[key]
    path = "/movies?" + urlencode({"collection": name, "sort": 2})
    root = Tree(page).root
    form = root.find(id="main", tag="form")
    if (
        form is None
        or form.attrs.get("method", "").lower() != "post"
        or form.attrs.get("enctype", "").lower() != "multipart/form-data"
        or form.attrs.get("action", "") not in {"", path}
    ):
        raise LibraryFormatError("Collection removal form was not recognized")
    button = form.find(id=button_id, tag="button")
    if (
        button is None
        or "disabled" in button.attrs
        or button.attrs.get("onclick", "").strip()
        != f"submit_parent_form_once( event, '{action}' );"
    ):
        raise LibraryFormatError("Collection removal action was not recognized")
    controls = [
        node
        for node in form.nodes()
        if node.tag == "input"
        and node.attrs.get("name") == "check-" + handle
        and node.attrs.get("type", "").lower() == "checkbox"
        and "disabled" not in node.attrs
    ]
    if len(controls) != 1 or controls[0].attrs.get("value", "on") != "on":
        raise LibraryFormatError("Selected version cannot be removed")
    return path, action


def _removal_path(path: str) -> bool:
    """Allow only canonical fixed collection views, with no extra parameters."""
    parts = urlsplit(path)
    try:
        query = parse_qs(parts.query, strict_parsing=True)
    except ValueError:
        return False
    if parts.scheme or parts.netloc or parts.fragment or parts.path != "/movies":
        return False
    if set(query) != {"collection", "sort"} or query["sort"] != ["2"]:
        return False
    return any(
        query["collection"] == [name]
        and path == "/movies?" + urlencode({"collection": name, "sort": 2})
        for name, _, _ in REMOVAL_ACTIONS.values()
    )


class LocalLibrary:
    """Read and update the local movie browser through one injected transport."""

    def __init__(self, transport: BrowserTransport):
        self._transport = transport

    async def movies(self) -> tuple[list[dict], list[str] | None]:
        """Return a complete catalog and available collection names."""
        return catalog(_text(await self._transport.get("/movies")))

    async def collection(self, name: str) -> list[dict]:
        """Return one complete collection listing."""
        name = clean(name, 256)
        if not name:
            raise ValueError("Empty collection")
        path = "/movies?" + urlencode({"collection": name, "sort": 2})
        rows, _ = catalog(_text(await self._transport.get(path)))
        return rows

    async def version_details(self, handle: str) -> dict:
        """Return metadata for one exact version handle."""
        identity(handle)
        path = "/details?" + urlencode({"id": handle, "callback": "true"})
        return details(_text(await self._transport.get(path)))

    async def version_collections(self, handle: str) -> tuple[str, list[str]]:
        """Read explicit collection memberships for one exact version."""
        identity(handle)
        path = "/details?" + urlencode({"id": handle, "callback": "true"})
        return detail_collections(_text(await self._transport.get(path)))

    async def artwork(self, path: str) -> tuple[bytes, str]:
        """Return validated image bytes from an allowed local cover route."""
        if artwork_path(path) != path:
            raise ValueError("Invalid artwork path")
        response = await self._transport.get(path)
        if (
            response.status != 200
            or response.content_type.lower() not in IMAGE_TYPES
            or response.content_encoding.lower() != "identity"
            or len(response.body) > MAX_ARTWORK
            or _image_type(response.body) != response.content_type.lower()
        ):
            raise BrowserResponseError("Invalid artwork response")
        return response.body, response.content_type.lower()

    async def add_targets(self, handle: str) -> dict[str, str]:
        """Read the current add form; values are opaque internal choice IDs."""
        return form_targets(_text(await self._transport.get("/movies")), handle)

    async def add_to_collection(
        self,
        handle: str,
        collection: str,
        *,
        pre_submit: Callable[[], None] | None = None,
    ) -> CollectionPostResult:
        """Submit one offered add-only form; never retry or infer membership."""
        identity(handle)
        collection = _collection_name(collection)
        page = _text(await self._transport.get("/movies"))
        rows, _ = catalog(page)
        if not any(row["source_handle"] == handle for row in rows):
            raise LibraryFormatError("Selected version is absent from listing")
        choices = form_targets(page, handle)
        if collection not in choices:
            raise LibraryFormatError("Collection is not an offered add target")
        fields = {
            "submit_type": "Add-To-Collection",
            "add_to_collection_select": choices[collection],
            "check-" + handle: "on",
        }
        # A synchronous refusal here proves no POST was attempted. Keep it
        # outside the possible-send exception boundary and local to this call.
        if pre_submit is not None:
            pre_submit()
        try:
            response = await self._transport.post("/movies", fields)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as error:
            raise BrowserOutcomeUnknownError(
                "Collection POST outcome is unknown"
            ) from error
        if (
            response.status not in {200, 302, 303}
            or response.content_encoding.lower() != "identity"
            or len(response.body) > MAX_PAGE
            or (
                response.status != 200
                and response.location not in {"/movies", "movies"}
            )
        ):
            raise BrowserOutcomeUnknownError("Collection POST outcome is unknown")
        return CollectionPostResult(response.status, response.location)

    async def remove_from_collection(
        self,
        handle: str,
        collection: str,
        *,
        pre_submit: Callable[[], None] | None = None,
    ) -> CollectionPostResult:
        """Submit one validated built-in removal/move; never retry the POST."""
        identity(handle)
        if not isinstance(collection, str):
            raise TypeError("Collection name must be text")
        key = re.sub(r"[\s_-]+", "", collection.casefold())
        if key not in REMOVAL_ACTIONS:
            raise ValueError("Unsupported collection removal")
        name = REMOVAL_ACTIONS[key][0]
        path = "/movies?" + urlencode({"collection": name, "sort": 2})
        page = _text(await self._transport.get(path))
        rows, _ = catalog(page)
        if not any(row["source_handle"] == handle for row in rows):
            raise LibraryFormatError("Selected version is absent from collection")
        _, action = removal_form(page, handle, name)
        # A synchronous refusal here proves no POST was attempted. Keep it
        # outside the possible-send exception boundary and local to this call.
        if pre_submit is not None:
            pre_submit()
        try:
            response = await self._transport.post(
                path, {"submit_type": action, "check-" + handle: "on"}
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as error:
            raise BrowserOutcomeUnknownError(
                "Collection POST outcome is unknown"
            ) from error
        if (
            response.status not in {200, 302, 303}
            or response.content_encoding.lower() != "identity"
            or len(response.body) > MAX_PAGE
            or (
                response.status != 200
                and response.location not in {"/movies", "movies", path}
            )
        ):
            raise BrowserOutcomeUnknownError("Collection POST outcome is unknown")
        return CollectionPostResult(response.status, response.location)


class _DirectBrowserTransport:
    """Direct, bounded HTTP transport for one private device address."""

    def __init__(
        self, host: str, port: int | None, *, tls: bool, timeout: float
    ) -> None:
        address = ipaddress.ip_address(host)
        if (
            not address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("Supply a literal private device IP")
        if port is None:
            port = 443 if tls else 80
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ValueError("Port must be between 1 and 65535")
        if not isinstance(tls, bool):
            raise TypeError("TLS mode must be a boolean")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Timeout must be a finite positive number")
        self.host = str(address)
        self.port = port
        self.tls = tls
        self.timeout = float(timeout)

    @staticmethod
    def _check_path(path: str) -> None:
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or len(path) > 2048
            or any(ord(char) < 33 or ord(char) > 126 or char in "\\#" for char in path)
        ):
            raise ValueError("Invalid local browser path")

    async def get(self, path: str) -> BrowserResponse:
        """Fetch one local page without redirects, cookies, or a proxy."""
        self._check_path(path)
        return await asyncio.wait_for(
            asyncio.to_thread(self._request, "GET", path, None, None),
            timeout=self.timeout + 5,
        )

    async def post(self, path: str, fields: dict[str, str]) -> BrowserResponse:
        """Send one bounded multipart request; never retry a POST."""
        self._check_path(path)
        if path != "/movies" and not _removal_path(path):
            raise ValueError("Invalid local browser POST path")
        boundary = secrets.token_hex(16)
        chunks: list[bytes] = []
        for name, value in fields.items():
            if not isinstance(name, str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]{1,256}", name
            ):
                raise ValueError("Invalid browser form field name")
            if (
                not isinstance(value, str)
                or len(value) > 512
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError("Invalid browser form field value")
            header = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
            )
            chunks.append((header + f"\r\n{value}\r\n").encode("utf-8"))
        body = b"".join(chunks) + f"--{boundary}--\r\n".encode("ascii")
        if len(body) > 64 * 1024:
            raise ValueError("Browser form exceeds size limit")
        return await asyncio.wait_for(
            asyncio.to_thread(
                self._request,
                "POST",
                path,
                body,
                f"multipart/form-data; boundary={boundary}",
            ),
            timeout=self.timeout + 5,
        )

    def _request(
        self, method: str, path: str, body: bytes | None, content_type: str | None
    ) -> BrowserResponse:
        connection_type = (
            http.client.HTTPSConnection if self.tls else http.client.HTTPConnection
        )
        connection = connection_type(self.host, self.port, timeout=self.timeout)
        headers = {"Accept-Encoding": "identity", "Connection": "close"}
        if content_type is not None:
            headers["Content-Type"] = content_type
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content_type_header = response.getheader("Content-Type", "")
            media_type, _, parameters = content_type_header.partition(";")
            charset = None
            if parameters.strip().lower().startswith("charset="):
                charset = parameters.split("=", 1)[1].strip().strip('"')
            return BrowserResponse(
                status=response.status,
                content_type=media_type.strip(),
                body=response.read(MAX_PAGE + 1),
                charset=charset,
                content_encoding=response.getheader("Content-Encoding", "identity"),
                location=response.getheader("Location"),
            )
        finally:
            connection.close()


class KaleidescapeServerBrowser(LocalLibrary):
    """Read a local server browser with a built-in direct HTTP transport.

    Pass a literal private IP address and optionally a port, TLS mode, and timeout.
    TLS uses the standard validating HTTPS connection and requires the device
    certificate to identify its IP address. A custom transport may be injected
    for testing or specialized network policy. Construction performs no I/O.
    """

    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        tls: bool = False,
        timeout: float = 15.0,
        transport: BrowserTransport | None = None,
    ) -> None:
        direct = _DirectBrowserTransport(host, port, tls=tls, timeout=timeout)
        self.host = direct.host
        self.port = direct.port
        self.tls = tls
        super().__init__(transport if transport is not None else direct)

    async def get_movies(self) -> list[dict]:
        """Read a fresh complete movie catalog."""
        movies, _ = await self.movies()
        return movies

    async def get_collections(self) -> list[str] | None:
        """Read available collection names; None means the UI is incomplete."""
        _, collections = await self.movies()
        return collections

    async def get_movie_details(self, handle: str) -> dict:
        """Read the exact version's details from the local browser."""
        return await self.version_details(handle)
