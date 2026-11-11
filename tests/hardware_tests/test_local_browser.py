"""Opt-in read-only checks against an owned server's local movie browser.

This module is excluded from default pytest recursion and never sends a POST.
"""

import os

import pytest

from kaleidescape.local_browser import KaleidescapeServerBrowser


@pytest.mark.asyncio
async def test_local_catalog_and_details_read_only():
    """Read a complete local listing and one detail page when available."""
    if os.getenv("PYK_HARDWARE") != "1" or not os.getenv("PYK_BROWSER_HOST"):
        pytest.skip("Set PYK_HARDWARE=1 and PYK_BROWSER_HOST")
    host = os.environ["PYK_BROWSER_HOST"]
    port = int(os.getenv("PYK_BROWSER_PORT", "80"))
    browser = KaleidescapeServerBrowser(host, port)
    rows = await browser.get_movies()
    collections = await browser.get_collections()
    assert all(row["source_handle"] for row in rows)
    assert collections is None or isinstance(collections, list)
    if rows:
        result = await browser.get_movie_details(rows[0]["source_handle"])
        assert "actors" in result
