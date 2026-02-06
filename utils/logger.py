import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from .log_buffer import LogBuffer, LogHandler

# Default logging level
LOGGING_LEVEL = getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO)

# Log file path
LOG_FILE_PATH = Path(os.getenv("LOG_FILE_PATH") or "data/tts.log")


class LoggingFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__()
        self.default_time_format = "%Y-%m-%d %H:%M:%S"
        self.black = "\x1b[30m"
        self.red = "\x1b[31m"
        self.green = "\x1b[32m"
        self.yellow = "\x1b[33m"
        self.blue = "\x1b[34m"
        self.gray = "\x1b[38m"
        self.purple = "\x1b[35m\x1b[34m"
        self.cyan = "\x1b[36m"
        self.reset = "\x1b[0m"
        self.bold = "\x1b[1m"
        self.COLORS = {
            logging.DEBUG: self.gray + self.bold,
            logging.INFO: self.blue + self.bold,
            logging.WARNING: self.yellow + self.bold,
            logging.ERROR: self.red,
            logging.CRITICAL: self.red + self.bold,
        }

    def format(self, record: logging.LogRecord) -> str:
        log_color = self.COLORS.get(record.levelno, self.blue + self.bold)
        fmt = "(black){asctime}(reset) (levelcolor){levelname:<8}(reset) (green){name}(reset)  (cyan){message}"
        fmt = fmt.replace("(black)", self.black + self.bold)
        fmt = fmt.replace("(reset)", self.reset)
        fmt = fmt.replace("(levelcolor)", log_color)
        fmt = fmt.replace("(green)", self.green + self.bold)
        fmt = fmt.replace("(cyan)", self.cyan)
        formatter = logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S", style="{")
        return formatter.format(record)


class PlainFormatter(logging.Formatter):
    """Plain text formatter for file/web logging (no ANSI codes)."""

    def __init__(self) -> None:
        super().__init__(
            fmt="{asctime} {levelname:<8} {name}  {message}",
            datefmt="%Y-%m-%d %H:%M:%S",
            style="{",
        )


_console_handler: Optional[logging.Handler] = None
_file_handler: Optional[logging.Handler] = None
_web_handler: Optional[logging.Handler] = None
_web_buffer: Optional[LogBuffer] = None


def _ensure_console_handler() -> logging.Handler:
    global _console_handler
    if _console_handler is not None:
        return _console_handler

    handler = logging.StreamHandler()
    handler.setLevel(LOGGING_LEVEL)
    handler.setFormatter(LoggingFormatter())
    _console_handler = handler
    return handler


def _ensure_file_handler() -> logging.Handler:
    global _file_handler
    if _file_handler is not None:
        return _file_handler

    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    handler.setLevel(LOGGING_LEVEL)
    handler.setFormatter(PlainFormatter())
    _file_handler = handler
    return handler


def init_root_logging(loop: asyncio.AbstractEventLoop) -> LogBuffer:
    """Initialize root logging and return the in-memory web log buffer."""

    global _web_handler, _web_buffer

    root_logger = logging.getLogger()
    root_logger.setLevel(LOGGING_LEVEL)

    if LOGGING_LEVEL > logging.DEBUG:
        logging.getLogger("discord").setLevel(logging.WARNING)
        logging.getLogger("discord.voice_state").setLevel(logging.WARNING)
        logging.getLogger("discord.gateway").setLevel(logging.WARNING)

    # Root gets console + file so dependency logs are visible too.
    for h in (_ensure_console_handler(), _ensure_file_handler()):
        if h not in root_logger.handlers:
            root_logger.addHandler(h)

    if _web_handler is None or _web_buffer is None:
        max_lines = int(os.getenv("WEB_LOG_MAX_LINES") or "1000")
        _web_buffer = LogBuffer(loop=loop, max_lines=max_lines)
        _web_handler = LogHandler(_web_buffer)
        _web_handler.setLevel(LOGGING_LEVEL)
        _web_handler.setFormatter(PlainFormatter())

    if _web_handler not in root_logger.handlers:
        root_logger.addHandler(_web_handler)

    # If some TTS loggers were created before the root web handler existed,
    # attach it now so the Web UI still sees their logs.
    for name, obj in logging.Logger.manager.loggerDict.items():
        if not name.startswith("TTS."):
            continue
        if not isinstance(obj, logging.Logger):
            continue
        if _web_handler not in obj.handlers:
            obj.addHandler(_web_handler)

    return _web_buffer


def set_logger(logger: logging.Logger) -> logging.Logger:
    """Configure a logger with console/file handlers and the Web UI handler."""

    logger.setLevel(LOGGING_LEVEL)
    logger.propagate = False

    for h in (_ensure_console_handler(), _ensure_file_handler()):
        if h not in logger.handlers:
            logger.addHandler(h)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if handler.__class__.__name__ == "LogHandler" and handler not in logger.handlers:
            logger.addHandler(handler)

    return logger


def get_logger(module: str) -> logging.Logger:
    """Get a logger named like `TTS.<module>` with handlers attached."""

    return set_logger(logging.getLogger(f"TTS.{module}"))


def get_last_log_lines(count: int = 500) -> str:
    """Read the last N lines from the log file."""

    if count <= 0:
        return ""
    if not LOG_FILE_PATH.exists():
        return "No log file found."

    try:
        return "".join(_tail_file(LOG_FILE_PATH, count))
    except Exception as exc:
        return f"Error reading logs: {exc}"


def _tail_file(path: Path, count: int) -> list[str]:
    # Efficient-ish tail implementation: read from the end in blocks.
    # Falls back to full read for small files.
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        if end == 0:
            return []

        block_size = 8192
        blocks: list[bytes] = []
        remaining = end
        newline_count = 0

        while remaining > 0 and newline_count <= count:
            read_size = block_size if remaining >= block_size else remaining
            remaining -= read_size
            f.seek(remaining)
            data = f.read(read_size)
            blocks.append(data)
            newline_count += data.count(b"\n")

        content = b"".join(reversed(blocks))
        # Splitlines keeps trailing empty line consistent.
        lines = content.splitlines(keepends=True)
        return [line.decode("utf-8", errors="replace") for line in lines[-count:]]
