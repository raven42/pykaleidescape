"""Prepare a movie launch without sending it unless --send is supplied.

Run: uv run python -m examples.prepared_movie_launch PLAYER_HOST SERIAL HANDLE
Add --send only when an experimental PLAY_MOVIE is intended. An ACK is not
proof that the requested edition began playback.
"""

import argparse
import asyncio

from kaleidescape.device import Device


async def prepare_or_send(
    device: Device, serial: str, handle: str, *, send: bool = False
) -> None:
    """Read exact movie metadata, then optionally send one experimental launch."""
    await device.connect()
    try:
        prepared = await device.prepare_movie_launch(handle, serial=serial)
        print(f"Prepared {prepared.title} ({prepared.handle})")
        if send:
            acknowledgement = await device.send_movie_launch(
                prepared, allow_experimental=True
            )
            print(f"PLAY_MOVIE ACK status: {acknowledgement.status}")
            print(
                "Verify playback and edition on the player; do not retry an uncertain send."
            )
        else:
            print("Read-only preparation complete; no PLAY_MOVIE command sent")
    finally:
        await device.disconnect()


def main() -> None:
    """Keep the experimental send behind an explicit CLI flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="local player host")
    parser.add_argument("serial", help="player serial from read_identity()")
    parser.add_argument("handle", help="exact qualified movie handle")
    parser.add_argument("--send", action="store_true", help="send one PLAY_MOVIE")
    args = parser.parse_args()
    asyncio.run(
        prepare_or_send(
            Device(args.host, reconnect=False), args.serial, args.handle, send=args.send
        )
    )


if __name__ == "__main__":
    main()
