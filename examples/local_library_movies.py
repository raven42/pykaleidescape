"""List movies from a server's local browser using a private IP address.

Run: uv run python -m examples.local_library_movies 192.168.1.50
Optional: --port 80 --details HANDLE
The library owns the direct HTTP transport. This example makes read requests only.
"""

import argparse
import asyncio

from kaleidescape.local_browser import KaleidescapeServerBrowser


async def show_movies(
    browser: KaleidescapeServerBrowser, details_handle: str | None = None
) -> None:
    """Print the movie listing, collections, and optional exact-version details."""
    movies = await browser.get_movies()
    collections = await browser.get_collections()
    print(f"Movies: {len(movies)}; collections: {collections}")
    for movie in movies:
        print(f"{movie['title']} [{movie['source_handle']}]")
    if details_handle is not None:
        print(
            f"Details for {details_handle}: {await browser.get_movie_details(details_handle)}"
        )


def main() -> None:
    """Require a literal private IP before contacting the local browser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip_address", help="private IP address of a local server")
    parser.add_argument("--port", type=int, help="local HTTP port (default: 80)")
    parser.add_argument(
        "--tls", action="store_true", help="use verified HTTPS (default port: 443)"
    )
    parser.add_argument("--details", help="exact version handle to inspect")
    args = parser.parse_args()
    browser = KaleidescapeServerBrowser(args.ip_address, args.port, tls=args.tls)
    asyncio.run(show_movies(browser, args.details))


if __name__ == "__main__":
    main()
