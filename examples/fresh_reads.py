"""Read current device identity and state, optionally discovering related devices.

Run: uv run python -m examples.fresh_reads PLAYER_OR_SERVER_HOST [--discover]
The command sends only read requests. Output includes device metadata.
"""

import argparse
import asyncio

from kaleidescape.device import Device


async def inspect_device(device: Device, discover: bool = False) -> None:
    """Read one connected component and optionally discover routed components."""
    await device.connect()
    try:
        identity = await device.read_identity()
        state = await device.read_control_state()
        print(f"Model: {identity.model}; serial: {identity.serial_number}")
        print(
            f"Movie zones: {identity.movie_zones}; music zones: {identity.music_zones}"
        )
        print(f"Power: {state.power}; readiness: {state.readiness}")
        if identity.movie_zones:
            print(f"Play status: {state.play_status}; title: {state.playing_title}")
            print(f"Highlighted selection: {await device.get_highlighted_selection()}")
        if discover:
            for component in await device.discover_devices():
                print(f"Discovered: {component.model} ({component.serial_number})")
    finally:
        await device.disconnect()


def main() -> None:
    """Parse a device host and perform read-only inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="local player or server host")
    parser.add_argument(
        "--discover", action="store_true", help="read related identities"
    )
    args = parser.parse_args()
    asyncio.run(inspect_device(Device(args.host, reconnect=False), args.discover))


if __name__ == "__main__":
    main()
