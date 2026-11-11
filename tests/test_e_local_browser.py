"""Fixed browser routes, response bounds, and one-shot collection submission."""

# pylint: disable=missing-function-docstring,protected-access

import asyncio

import pytest

from kaleidescape.library_parser import LibraryFormatError
from kaleidescape.local_browser import (
    MAX_PAGE,
    BrowserOutcomeUnknownError,
    BrowserResponse,
    BrowserResponseError,
    KaleidescapeServerBrowser,
    LocalLibrary,
    removal_form,
)

HANDLE = "26-0.1"
PAGE = b"""
<span id="movie_count_str">1 movie</span>
<table><tr class="movie_container" selection_handle="26-0.1"
 selection_group="one"><td class="movie_title">A Movie</td></tr></table>
<form id="main" method="post" enctype="multipart/form-data" action="/movies">
 <input id="add_to_collection_select_dropdown_value"
  name="add_to_collection_select" type="hidden">
 <input type="checkbox" name="check-26-0.1" value="on">
 <button id="add_to_collection"
  onclick="submit_parent_form_once( event, 'Add-To-Collection' );">Add</button>
 <ul id="add_to_collection_select_dropdown">
  <li class="dropdown_option">Favorites<input value="internal-42"></li>
 </ul>
</form>
"""


def removal_page(button_id, action):
    return PAGE.replace(b' action="/movies"', b"").replace(
        b"</form>",
        f'<button id="{button_id}" onclick="submit_parent_form_once( event, '
        f"'{action}' );\">Remove</button></form>".encode(),
    )


@pytest.mark.parametrize(
    ("collection", "name", "button", "action"),
    [
        ("Favorites", "Favorites", "remove_from_favorites", "Remove-Often"),
        ("Watch Soon", "WatchSoon", "remove_from_watch_soon", "Remove-Soon"),
        ("New", "New", "remove_from_new", "Remove-New"),
        ("Played", "Played", "remove_from_played", "Remove-Played"),
        ("Paused", "Paused", "remove_from_paused", "Remove-Paused"),
    ],
)
@pytest.mark.asyncio
async def test_builtin_removal_submits_only_selected_version(
    collection, name, button, action
):
    page = removal_page(button, action)

    class Transport(FakeTransport):
        """Serve the selected fictional collection page."""

        async def get(self, path):
            self.get_paths.append(path)
            return BrowserResponse(200, "text/html", page)

    transport = Transport()
    await LocalLibrary(transport).remove_from_collection(HANDLE, collection)
    path = f"/movies?collection={name}&sort=2"
    assert transport.get_paths == [path]
    assert transport.posts == [(path, {"submit_type": action, "check-" + HANDLE: "on"})]


def test_removal_requires_exact_form_checkbox_and_action():
    page = removal_page("remove_from_played", "Remove-Played").decode()
    assert removal_form(page, HANDLE, "Played") == (
        "/movies?collection=Played&sort=2",
        "Remove-Played",
    )
    for changed in (
        page.replace("Remove-Played", "Remove-New"),
        page.replace("remove_from_played", "remove_from_new"),
        page.replace('name="check-26-0.1"', 'name="check-other"'),
        page.replace(
            'enctype="multipart/form-data"',
            'enctype="multipart/form-data" action="https://bad.invalid/"',
        ),
        page.replace('type="checkbox"', 'type="checkbox" disabled'),
    ):
        with pytest.raises(LibraryFormatError):
            removal_form(changed, HANDLE, "Played")
    with pytest.raises(ValueError):
        removal_form(page, HANDLE, "All")


@pytest.mark.asyncio
async def test_removal_unknown_post_is_not_retried():
    class Transport(FakeTransport):
        """Serve a fictional collection and lose the POST response."""

        async def get(self, path):
            return BrowserResponse(
                200, "text/html", removal_page("remove_from_played", "Remove-Played")
            )

    transport = Transport()
    transport.fail_post = True
    with pytest.raises(BrowserOutcomeUnknownError):
        await LocalLibrary(transport).remove_from_collection(HANDLE, "Played")
    assert len(transport.posts) == 1


class FakeTransport:
    """Caller-owned transport with no real network access."""

    def __init__(self):
        self.get_paths = []
        self.posts = []
        self.fail_post = False

    async def get(self, path):
        self.get_paths.append(path)
        if path.startswith("/details?"):
            return BrowserResponse(
                200, "text/html", b'<h1 id="movie_title">A Movie</h1>'
            )
        if path == "/webcovers/a.jpg":
            return BrowserResponse(200, "image/jpeg", b"\xff\xd8\xffmock")
        return BrowserResponse(200, "text/html", PAGE)

    async def post(self, path, fields):
        self.posts.append((path, fields))
        if self.fail_post:
            raise TimeoutError
        return BrowserResponse(302, "text/html", b"", location="/movies")


@pytest.mark.parametrize("operation", ["add", "remove"])
@pytest.mark.parametrize("outcome", ["allowed", "denied", "post_failed", "invalid"])
@pytest.mark.asyncio
async def test_pre_submit_runs_after_validation_and_before_one_post(operation, outcome):
    """A caller can refuse a validated write without an uncertain outcome."""
    events = []

    class Transport(FakeTransport):
        """Record the order of reads and the possible write."""

        async def get(self, path):
            events.append("get")
            page = (
                removal_page("remove_from_played", "Remove-Played")
                if operation == "remove"
                else PAGE
            )
            if outcome == "invalid":
                page = page.replace(b"check-26-0.1", b"check-other")
            return BrowserResponse(200, "text/html", page)

        async def post(self, path, fields):
            events.append("post")
            self.posts.append((path, fields))
            if outcome == "post_failed":
                raise TimeoutError
            return BrowserResponse(302, "text/html", b"", location="/movies")

    def pre_submit():
        events.append("authorize")
        if outcome == "denied":
            raise PermissionError("Access changed")

    transport = Transport()
    library = LocalLibrary(transport)
    if operation == "add":
        request = library.add_to_collection(HANDLE, "Favorites", pre_submit=pre_submit)
    else:
        request = library.remove_from_collection(
            HANDLE, "Played", pre_submit=pre_submit
        )

    if outcome == "invalid":
        with pytest.raises(LibraryFormatError):
            await request
        assert events == ["get"]
    elif outcome == "denied":
        with pytest.raises(PermissionError, match="Access changed"):
            await request
        assert events == ["get", "authorize"]
    elif outcome == "post_failed":
        with pytest.raises(BrowserOutcomeUnknownError):
            await request
        assert events == ["get", "authorize", "post"]
    else:
        assert (await request).status == 302
        assert events == ["get", "authorize", "post"]
    assert len(transport.posts) == (1 if outcome in {"allowed", "post_failed"} else 0)


@pytest.mark.asyncio
async def test_fixed_read_routes():
    transport = FakeTransport()
    library = LocalLibrary(transport)
    rows, _ = await library.movies()
    assert rows[0]["source_handle"] == HANDLE
    assert (await library.version_details(HANDLE))["artwork_path"] is None
    body, image_type = await library.artwork("/webcovers/a.jpg")
    assert body.startswith(b"\xff\xd8\xff")
    assert image_type == "image/jpeg"
    assert transport.get_paths == [
        "/movies",
        "/details?id=26-0.1&callback=true",
        "/webcovers/a.jpg",
    ]
    with pytest.raises(ValueError):
        await library.artwork("https://other.invalid/image.jpg")


@pytest.mark.asyncio
async def test_version_collection_details_use_exact_handle():
    """Read the explicit per-version collection field from the details route."""

    class Transport(FakeTransport):
        """Return a fictional details response."""

        async def get(self, path):
            self.get_paths.append(path)
            return BrowserResponse(
                200,
                "text/html",
                b'<h1 id="movie_title">A Movie</h1><table><tr>'
                b'<td class="details_name">In the following collections:</td>'
                b'<td class="details_value"><a>Played</a></td></tr></table>',
            )

    transport = Transport()
    assert await LocalLibrary(transport).version_collections(HANDLE) == (
        "A Movie",
        ["Played"],
    )
    assert transport.get_paths == ["/details?id=26-0.1&callback=true"]


@pytest.mark.asyncio
async def test_live_form_submits_exactly_once():
    transport = FakeTransport()
    library = LocalLibrary(transport)
    result = await library.add_to_collection(HANDLE, "Favorites")
    assert result.status == 302
    assert transport.posts == [
        (
            "/movies",
            {
                "submit_type": "Add-To-Collection",
                "add_to_collection_select": "internal-42",
                "check-26-0.1": "on",
            },
        )
    ]


@pytest.mark.asyncio
async def test_unoffered_or_failed_post_never_retried():
    transport = FakeTransport()
    library = LocalLibrary(transport)
    with pytest.raises(Exception, match="offered"):
        await library.add_to_collection(HANDLE, "Missing")
    assert not transport.posts
    transport.fail_post = True
    with pytest.raises(BrowserOutcomeUnknownError):
        await library.add_to_collection(HANDLE, "Favorites")
    assert len(transport.posts) == 1


@pytest.mark.asyncio
async def test_rejects_compressed_or_wrong_type_page():
    class BadTransport(FakeTransport):
        """Return a compressed response with the wrong content type."""

        async def get(self, path):
            return BrowserResponse(
                200, "application/json", PAGE, content_encoding="gzip"
            )

    with pytest.raises(BrowserResponseError):
        await LocalLibrary(BadTransport()).movies()


@pytest.mark.asyncio
async def test_cancelled_post_has_uncertain_outcome_without_retry():
    """Once handed to transport, cancellation cannot prove a POST was unsent."""

    class CancelTransport(FakeTransport):
        """Raise cancellation after recording the attempted POST."""

        async def post(self, path, fields):
            self.posts.append((path, fields))
            raise asyncio.CancelledError

    transport = CancelTransport()
    with pytest.raises(BrowserOutcomeUnknownError):
        await LocalLibrary(transport).add_to_collection(HANDLE, "Favorites")
    assert len(transport.posts) == 1


@pytest.mark.asyncio
async def test_server_browser_read_helpers_use_existing_library_parser():
    transport = FakeTransport()
    browser = KaleidescapeServerBrowser("192.168.1.60", transport=transport)
    assert [movie["source_handle"] for movie in await browser.get_movies()] == [HANDLE]
    assert await browser.get_collections() is None
    assert (await browser.get_movie_details(HANDLE))["artwork_path"] is None
    assert transport.get_paths == [
        "/movies",
        "/movies",
        "/details?id=26-0.1&callback=true",
    ]


def test_server_browser_validates_endpoint_before_network_access():
    for address in ("8.8.8.8", "127.0.0.1", "device.example"):
        with pytest.raises(ValueError):
            KaleidescapeServerBrowser(address)
    with pytest.raises(ValueError, match="Port"):
        KaleidescapeServerBrowser("192.168.1.60", 0)
    with pytest.raises(ValueError, match="Timeout"):
        KaleidescapeServerBrowser("192.168.1.60", timeout=0)
    browser = KaleidescapeServerBrowser("192.168.1.60", tls=True)
    assert browser.host == "192.168.1.60"
    assert browser.port == 443


@pytest.mark.asyncio
async def test_server_browser_builtin_get_is_bounded_and_direct(monkeypatch):
    calls = []

    class FakeResponse:
        """Serve a bounded HTML response without a socket."""

        status = 200

        def getheader(self, name, default=None):
            return {"Content-Type": "text/html; charset=utf-8"}.get(name, default)

        def read(self, limit):
            calls.append(("read", limit))
            return PAGE

    class FakeConnection:
        """Record direct HTTP requests."""

        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def request(self, method, path, body=None, headers=None):
            calls.append(("request", method, path, body, headers))

        def getresponse(self):
            return FakeResponse()

        def close(self):
            calls.append("close")

    monkeypatch.setattr(
        "kaleidescape.local_browser.http.client.HTTPConnection", FakeConnection
    )
    browser = KaleidescapeServerBrowser("192.168.1.60")
    assert len(await browser.get_movies()) == 1
    assert calls[0] == ("connect", "192.168.1.60", 80, 15.0)
    assert calls[1][0:3] == ("request", "GET", "/movies")
    assert calls[1][4] == {"Accept-Encoding": "identity", "Connection": "close"}
    assert ("read", MAX_PAGE + 1) in calls
    assert calls[-1] == "close"
    with pytest.raises(ValueError, match="path"):
        await browser._transport.get("//other/movies")


@pytest.mark.asyncio
async def test_server_browser_tls_uses_https_connection(monkeypatch):
    calls = []

    class FakeResponse:
        """Return a small listing over a simulated HTTPS connection."""

        status = 200

        def getheader(self, name, default=None):
            return {"Content-Type": "text/html"}.get(name, default)

        def read(self, _limit):
            return PAGE

    class FakeHttpsConnection:
        """Record HTTPS construction without opening a socket."""

        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def request(self, method, path, body=None, headers=None):
            calls.append((method, path, body, headers))

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(
        "kaleidescape.local_browser.http.client.HTTPSConnection", FakeHttpsConnection
    )
    browser = KaleidescapeServerBrowser("192.168.1.60", tls=True)
    assert len(await browser.get_movies()) == 1
    assert calls[0] == ("192.168.1.60", 443, 15.0)


@pytest.mark.asyncio
async def test_server_browser_builtin_post_is_one_multipart_request(monkeypatch):
    calls = []

    class FakeResponse:
        """Reply with the listing or a redirect acknowledgement."""

        def __init__(self, method):
            self.status = 302 if method == "POST" else 200
            self.method = method

        def getheader(self, name, default=None):
            values = {"Content-Type": "text/html", "Location": "/movies"}
            return values.get(name, default)

        def read(self, _limit):
            return b"" if self.method == "POST" else PAGE

    class FakeConnection:
        """Record one GET followed by one POST."""

        def __init__(self, host, port, timeout):
            self.method = ""
            calls.append(("connect", host, port, timeout))

        def request(self, method, path, body=None, headers=None):
            self.method = method
            calls.append((method, path, body, headers))

        def getresponse(self):
            return FakeResponse(self.method)

        def close(self):
            pass

    monkeypatch.setattr(
        "kaleidescape.local_browser.http.client.HTTPConnection", FakeConnection
    )
    browser = KaleidescapeServerBrowser("192.168.1.60")
    assert (await browser.add_to_collection(HANDLE, "Favorites")).status == 302
    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][1] == "/movies"
    assert posts[0][3]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="check-26-0.1"\r\n\r\non\r\n' in posts[0][2]
    assert b'name="add_to_collection_select"\r\n\r\ninternal-42\r\n' in posts[0][2]
