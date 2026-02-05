import asyncio
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Set, Tuple


@dataclass
class LogSubscription:
    queue: asyncio.Queue
    initial_lines: List[str]


class LogBuffer:
    def __init__(self, loop: asyncio.AbstractEventLoop, max_lines: int = 1000) -> None:
        self._loop = loop
        self._lines: Deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._subscribers: Set[asyncio.Queue] = set()

    def get_lines(self, tail: Optional[int] = None) -> List[str]:
        with self._lock:
            lines = list(self._lines)
        if tail is None:
            return lines
        if tail <= 0:
            return []
        return lines[-tail:]

    def subscribe(self, max_queue: int = 200, tail: int = 500) -> LogSubscription:
        q: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        with self._lock:
            self._subscribers.add(q)
            initial = list(self._lines)[-tail:]
        return LogSubscription(queue=q, initial_lines=initial)

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def append(self, line: str) -> None:
        # Can be called from any thread.
        with self._lock:
            self._lines.append(line)
            subscribers = list(self._subscribers)

        def fanout() -> None:
            for q in subscribers:
                try:
                    q.put_nowait(line)
                except asyncio.QueueFull:
                    # Drop lines for slow consumers.
                    pass

        if self._loop.is_running():
            self._loop.call_soon_threadsafe(fanout)


class LogHandler(logging.Handler):
    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            # Best-effort, never break logging.
            msg = record.getMessage()
        self._buffer.append(msg)


# Backwards-compatible alias
LogBufferHandler = LogHandler


def setup_logging(loop: asyncio.AbstractEventLoop) -> LogBuffer:
    log_level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, log_level_name, logging.INFO)

    buffer = LogBuffer(loop=loop, max_lines=int(os.getenv("WEB_LOG_MAX_LINES") or "1000"))
    handler = LogHandler(buffer)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    root = logging.getLogger()
    root.addHandler(handler)
    return buffer
