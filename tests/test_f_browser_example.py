"""Run the local browser example with fake responses, without hardware."""

import pytest

from examples.local_library_movies import show_movies
from kaleidescape.local_browser import BrowserResponse, KaleidescapeServerBrowser

MOVIES_PAGE = b"""
<span id="movie_count_str">1 movie</span>
<table><tr class="movie_container" selection_handle="26-0.1"
 selection_group="one"><td class="movie_title">A Movie</td></tr></table>
"""
DETAILS_PAGE = b'<h1 id="movie_title">A Movie</h1>'


@pytest.mark.asyncio
async def test_movies_example_uses_fake_transport(capsys: pytest.CaptureFixture[str]):
    """The catalog example lists titles and optional details without network access."""

    class FakeTransport:
        """Return local response bytes and reject all writes."""

        def __init__(self):
            self.paths: list[str] = []

        async def get(self, path: str) -> BrowserResponse:
            """Return a saved response for one requested path."""
            self.paths.append(path)
            body = DETAILS_PAGE if path.startswith("/details?") else MOVIES_PAGE
            return BrowserResponse(200, "text/html", body)

        async def post(self, path: str, fields: dict[str, str]) -> BrowserResponse:
            """Reject all mutations in this fake transport."""
            raise AssertionError("A read-only example must never POST")

    transport = FakeTransport()
    await show_movies(
        KaleidescapeServerBrowser("192.168.1.60", transport=transport), "26-0.1"
    )
    assert transport.paths == ["/movies", "/movies", "/details?id=26-0.1&callback=true"]
    output = capsys.readouterr().out
    assert "Movies: 1" in output
    assert "A Movie [26-0.1]" in output
    assert "Details for 26-0.1" in output
