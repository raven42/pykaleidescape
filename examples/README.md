# Device examples

Run these from the repository root with `uv run python -m examples.<name>`.
They use the existing `Device.connect() → methods → disconnect()` workflow and
require no new package dependency.

- `fresh_reads.py` reads identity and current state. `--discover` also reads
  identities reachable through the connected player or server; it sends only
  read requests and prints device metadata.
- `explicit_controls.py` sends exactly one selected playback, pause, page,
  details, search, or menu command.
- `prepared_movie_launch.py` performs read-only preparation by default. `--send`
  opts into one experimental `PLAY_MOVIE` command. An acknowledgement does not
  prove playback or exact edition; check the player and do not retry an
  uncertain send.

```sh
uv run python -m examples.fresh_reads 192.168.1.50 --discover
uv run python -m examples.explicit_controls 192.168.1.50 pause-on
uv run python -m examples.prepared_movie_launch 192.168.1.50 00000000123A 26-0.0-S_example
```

Replace the example address, serial and handle with values from owned devices.
