import asyncio


async def wait_for_playback(done: asyncio.Event, timeout: float) -> bool:
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
