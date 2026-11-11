"""Local browser parsing without network access or script execution."""

# pylint: disable=missing-function-docstring,protected-access

import pytest

from kaleidescape.library_parser import (
    LibraryFormatError,
    artwork_path,
    catalog,
    detail_collections,
    details,
)


def _listing(count: int = 1) -> str:
    return f"""
      <span id="movie_count_str">{count} movies</span><table>
      <tr class="movie_container" selection_handle="26-0.1" selection_group="one">
       <td class="movie_title">A Movie</td><td class="movie_media_type">4K</td>
      </tr></table>
      <div id="collection_pulldown">
       <a href="/movies?collection=Played">Played</a>
       <a href="/movies?collection=Paused">Paused</a>
      </div>
    """


def test_complete_listing_retains_exact_handle_and_collections():
    rows, collections = catalog(_listing())
    assert rows[0]["source_handle"] == "26-0.1"
    assert rows[0]["formats"] == ["4K"]
    assert collections == ["Paused", "Played"]


def test_incomplete_listing_rejected_before_return():
    with pytest.raises(LibraryFormatError):
        catalog(_listing(2))


def test_detail_parsing_and_artwork_route():
    page = """
      <h1 id="movie_title">A Movie</h1>
      <table><tr><td class="details_name">Cast:</td>
        <td class="details_value">First Actor, Second Actor</td></tr></table>
      <div id="misc_text">120 minutes</div>
      <div id="cover_art"><img src="/webcoverslarge/a/b.jpg"></div>
    """
    record = details(page)
    assert record["actors"] == ["First Actor", "Second Actor"]
    assert record["duration_seconds"] == 7200
    assert record["artwork_path"] == "/webcoverslarge/a/b.jpg"
    assert artwork_path("/webcovers/../private.jpg") is None
    assert artwork_path("https://example.invalid/a.jpg") is None


def test_oversized_page_rejected():
    with pytest.raises(LibraryFormatError):
        catalog("x" * (8 * 1024 * 1024 + 1))


def test_detail_collections_require_one_complete_explicit_field():
    """A missing or ambiguous field must never be mistaken for no memberships."""
    page = """<h1 id="movie_title">A Movie</h1><table><tr>
    <td class="details_name">In the following collections:</td>
    <td class="details_value"><a href="#">Played</a><a href="#">Watch Soon</a></td>
    </tr></table>"""
    assert detail_collections(page) == ("A Movie", ["Played", "Watch Soon"])
    for invalid in (
        page.replace("In the following collections", "Other"),
        page.replace('<a href="#">Played</a>', "").replace(
            '<a href="#">Watch Soon</a>', ""
        ),
        page.replace("Watch Soon", "Played"),
        page[: page.index("</td>", page.index('class="details_value"'))],
        page.replace(
            "</table>", page[page.index("<tr>") : page.index("</table>")] + "</table>"
        ),
    ):
        with pytest.raises(LibraryFormatError):
            detail_collections(invalid)
