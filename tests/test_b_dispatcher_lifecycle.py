"""Dispatcher task ownership without changing existing callback arguments."""

# pylint: disable=missing-function-docstring,protected-access

import asyncio

import pytest

from kaleidescape.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_async_listener_failure_is_isolated():
    dispatcher = Dispatcher()
    seen = asyncio.Event()

    async def failing(*args):
        assert args == ("MOVIE", ["fictional"])
        seen.set()
        raise RuntimeError("fictional failure")

    dispatcher.connect(failing)
    dispatcher.send("MOVIE", ["fictional"])
    await asyncio.wait_for(seen.wait(), 1)
    await dispatcher.async_close()
    assert not dispatcher._tasks


@pytest.mark.asyncio
async def test_explicit_close_cancels_pending_callbacks():
    dispatcher = Dispatcher()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def waiting(*args):
        assert args == ("MOVIE",)
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    dispatcher.connect(waiting)
    dispatcher.send("MOVIE")
    await asyncio.wait_for(entered.wait(), 1)
    await dispatcher.async_close()
    assert cancelled.is_set()
    assert not dispatcher._tasks
