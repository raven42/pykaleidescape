"""Send one selected modern movie-player control command.

Run: uv run python -m examples.explicit_controls PLAYER_HOST pause-on
The selected action changes playback or menu state. Run it only on your player.
"""

import argparse
import asyncio

from kaleidescape.device import Device


async def send_control(device: Device, action: str) -> None:
    """Send the named control once and disconnect even if it fails."""
    actions = {
        "pause-on": device.pause_on,
        "pause-off": device.pause_off,
        "back": device.back,
        "play-or-pause": device.play_or_pause,
        "page-up": device.page_up,
        "page-down": device.page_down,
        "details": device.details,
        "go-search": device.go_search,
        "menu-on": device.menu_on,
        "menu-off": device.menu_off,
    }
    if action not in actions:
        raise ValueError("Choose a supported control action")
    await device.connect()
    try:
        await actions[action]()
        print(f"{action} acknowledged; read fresh state to confirm the effect")
    finally:
        await device.disconnect()


def main() -> None:
    """Require an explicit action before connecting to a player."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="local player host")
    parser.add_argument(
        "action",
        choices=(
            "pause-on",
            "pause-off",
            "back",
            "play-or-pause",
            "page-up",
            "page-down",
            "details",
            "go-search",
            "menu-on",
            "menu-off",
        ),
    )
    args = parser.parse_args()
    asyncio.run(send_control(Device(args.host, reconnect=False), args.action))


if __name__ == "__main__":
    main()
