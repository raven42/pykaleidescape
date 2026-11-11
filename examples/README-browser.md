# Local library example

Run `uv run python -m examples.local_library_movies` from the repository root.
The example uses a literal private server IP and the built-in
`KaleidescapeServerBrowser` to read movies and collection names. Add
`--details HANDLE` for one exact version or `--tls` for verified HTTPS when the
certificate identifies the device IP. The example sends only GET requests.

```sh
uv run python -m examples.local_library_movies 192.168.1.60 --port 80
```

Replace the example address with an owned server. The API also supports
one-shot collection additions and validated removals; an acknowledgement does
not prove membership, so read it back before taking further action.

Direct library use:

```python
from kaleidescape.local_browser import KaleidescapeServerBrowser

browser = KaleidescapeServerBrowser("192.168.1.60", port=80)
movies = await browser.get_movies()
collections = await browser.get_collections()
if movies:
    details = await browser.get_movie_details(movies[0]["source_handle"])
```
