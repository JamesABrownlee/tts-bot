import asyncio
from typing import Tuple


async def enqueue_with_drop(queue: asyncio.Queue, item, policy: str = "drop_oldest") -> Tuple[int, bool]:
    dropped = 0
    if queue.full():
        if policy == "drop_oldest":
            try:
                _ = queue.get_nowait()
                queue.task_done()
                dropped = 1
            except asyncio.QueueEmpty:
                pass
        else:
            return 0, False
    await queue.put(item)
    return dropped, True
