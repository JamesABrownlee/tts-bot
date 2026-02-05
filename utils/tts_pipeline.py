import asyncio
import base64
import json
import queue
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import aiohttp

from .config import (
    FALLBACK_VOICE,
    GOOGLE_TTS_URL,
    TIKTOK_TTS_URL,
    USER_AGENT,
    VOICE_COOLDOWN_DURATION,
    VOICE_FAILURE_THRESHOLD,
)


class TTSAPIError(RuntimeError):
    def __init__(self, message: str, voice_id: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.voice_id = voice_id
        self.status_code = status_code


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int, reset_timeout: int) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.open_until = 0.0

    async def execute(self, func):
        now = time.monotonic()
        if self.open_until and now < self.open_until:
            raise TTSAPIError(f"{self.name} circuit open", "unknown")
        try:
            result = await func()
            self.failures = 0
            self.open_until = 0.0
            return result
        except Exception:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.open_until = now + self.reset_timeout
            raise


circuit_breakers = {
    "tiktok": CircuitBreaker("TikTok", failure_threshold=3, reset_timeout=60),
    "google": CircuitBreaker("Google", failure_threshold=5, reset_timeout=30),
}

failed_voices: dict[str, dict] = {}


def mark_voice_failed(voice_id: str) -> None:
    now = time.monotonic()
    status = failed_voices.get(voice_id, {"failures": 0, "cooldown_until": 0.0})
    status["failures"] += 1
    if status["failures"] >= VOICE_FAILURE_THRESHOLD:
        status["cooldown_until"] = now + VOICE_COOLDOWN_DURATION
    failed_voices[voice_id] = status


def mark_voice_success(voice_id: str) -> None:
    status = failed_voices.get(voice_id)
    if not status:
        return
    status["failures"] = max(0, status["failures"] - 1)
    if status["failures"] == 0:
        status["cooldown_until"] = 0.0


def is_voice_available(voice_id: str) -> bool:
    status = failed_voices.get(voice_id)
    if not status:
        return True
    now = time.monotonic()
    if status["cooldown_until"] and now >= status["cooldown_until"]:
        status["failures"] = 0
        status["cooldown_until"] = 0.0
        failed_voices[voice_id] = status
        return True
    return status["failures"] < VOICE_FAILURE_THRESHOLD


def is_google_voice(voice_id: str) -> bool:
    return voice_id == "google_translate" or voice_id.startswith("google_")


async def retry_with_backoff(func, max_retries: int = 2, base_delay: float = 0.5):
    for attempt in range(max_retries + 1):
        try:
            return await func(attempt)
        except Exception:
            if attempt >= max_retries:
                raise
            await asyncio.sleep(base_delay * (2**attempt))


@dataclass
class QueueStream:
    queue: queue.Queue
    buffer: bytearray
    closed: bool = False

    def feed(self, data: bytes) -> None:
        self.queue.put(data)

    def close(self) -> None:
        self.queue.put(None)

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        if size < 0:
            size = 65536

        while len(self.buffer) < size and not self.closed:
            chunk = self.queue.get()
            if chunk is None:
                self.closed = True
                break
            self.buffer.extend(chunk)

        if not self.buffer:
            return b""

        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data


def _json_find_string_value_start(buf: bytes, key: bytes) -> Optional[int]:
    """Best-effort JSON key lookup that returns the index *after* the opening quote.

    We only need enough parsing to locate `"data":"<base64...>"` in a minified response.
    """

    key_idx = buf.find(key)
    if key_idx < 0:
        return None

    i = key_idx + len(key)
    n = len(buf)

    # Skip whitespace
    while i < n and buf[i] in b" \t\r\n":
        i += 1
    if i >= n or buf[i] != 58:  # ':'
        return None
    i += 1

    while i < n and buf[i] in b" \t\r\n":
        i += 1
    if i >= n:
        return None

    if buf[i] == 110 and buf[i : i + 4] == b"null":  # 'n'
        return -1
    if buf[i] != 34:  # '"'
        return None
    return i + 1


async def _decode_tiktok_json_base64_stream(
    resp: aiohttp.ClientResponse,
    *,
    voice_id: str,
    stream: QueueStream,
) -> None:
    """Stream-decode the `"data":"<base64...>"` field and feed bytes into QueueStream."""

    # The response is typically minified JSON and `data` shows up early.
    prefix = bytearray()
    prefix_limit = 64 * 1024

    b64_buf = bytearray()
    saw_data = False
    fed_any = False

    def feed_decoded(b: bytes) -> None:
        nonlocal fed_any
        if b:
            fed_any = True
            stream.feed(b)

    def consume_b64_bytes(data: bytes) -> bool:
        """Consume base64 chars from `data`. Returns True when the JSON string ends."""

        nonlocal b64_buf
        end_quote = data.find(b"\"")
        if end_quote != -1:
            b64_buf.extend(data[:end_quote])
            try:
                feed_decoded(base64.b64decode(bytes(b64_buf)))
            except Exception as exc:
                raise TTSAPIError(f"Invalid TikTok base64: {exc}", voice_id) from exc
            b64_buf.clear()
            return True

        b64_buf.extend(data)
        # Decode only full 4-char base64 quanta to keep streaming.
        decode_len = (len(b64_buf) // 4) * 4
        if decode_len >= 4:
            chunk = bytes(b64_buf[:decode_len])
            del b64_buf[:decode_len]
            try:
                feed_decoded(base64.b64decode(chunk))
            except Exception as exc:
                raise TTSAPIError(f"Invalid TikTok base64: {exc}", voice_id) from exc
        return False

    async for chunk in resp.content.iter_chunked(4096):
        if not saw_data:
            prefix.extend(chunk)
            if len(prefix) > prefix_limit:
                # `data` should be near the start; avoid buffering huge responses on parse failure.
                raise TTSAPIError("TikTok JSON parse error (data field not found)", voice_id)

            start = _json_find_string_value_start(prefix, b"\"data\"")
            if start is None:
                continue
            if start == -1:
                raise TTSAPIError("TikTok returned null audio data", voice_id)

            saw_data = True
            # Everything after `start` is base64 until the next `"`.
            rest = bytes(prefix[start:])
            prefix.clear()
            if consume_b64_bytes(rest):
                break
            continue

        if consume_b64_bytes(chunk):
            break

    if not saw_data:
        # Small error bodies are fine to parse fully.
        try:
            payload = json.loads(prefix.decode("utf-8", errors="replace"))
            err = payload.get("error") or payload.get("message")
            if err:
                raise TTSAPIError(str(err), voice_id)
        except TTSAPIError:
            raise
        except Exception:
            pass
        raise TTSAPIError("No audio data", voice_id)

    # Flush any remaining base64 (should only happen if the stream ends unexpectedly).
    if b64_buf:
        try:
            feed_decoded(base64.b64decode(bytes(b64_buf)))
        except Exception as exc:
            raise TTSAPIError(f"Invalid TikTok base64: {exc}", voice_id) from exc
        b64_buf.clear()

    if not fed_any:
        raise TTSAPIError("TikTok returned empty audio data", voice_id)


async def _open_google_stream(text: str, voice_id: str, stream: QueueStream) -> asyncio.Task:
    timeout = aiohttp.ClientTimeout(total=15)
    params = {"ie": "UTF-8", "q": text, "tl": "en", "client": "tw-ob"}
    headers = {"User-Agent": USER_AGENT}

    session = aiohttp.ClientSession(timeout=timeout, headers=headers)
    try:
        resp = await session.get(GOOGLE_TTS_URL, params=params)
    except Exception:
        await session.close()
        raise

    if resp.status != 200:
        resp.release()
        await session.close()
        raise TTSAPIError(f"Google TTS HTTP {resp.status}", voice_id, resp.status)

    async def producer() -> None:
        try:
            async for chunk in resp.content.iter_chunked(4096):
                stream.feed(chunk)
        finally:
            stream.close()
            resp.release()
            await session.close()

    return asyncio.create_task(producer())


async def _open_tiktok_stream(text: str, voice_id: str, stream: QueueStream) -> asyncio.Task:
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"User-Agent": USER_AGENT}
    url = TIKTOK_TTS_URL
    payload = {"text": text, "voice": voice_id}

    session = aiohttp.ClientSession(timeout=timeout, headers=headers)
    resp: Optional[aiohttp.ClientResponse] = None
    try:
        for _ in range(6):
            resp = await session.post(url, json=payload, allow_redirects=False)
            if 300 <= resp.status < 400 and resp.headers.get("Location"):
                url = resp.headers["Location"]
                resp.release()
                resp = None
                continue
            break

        if resp is None:
            raise TTSAPIError("TikTok API no response", voice_id)

        if resp.status != 200:
            if resp.status >= 500:
                mark_voice_failed(voice_id)
            # Read a small snippet for debugging, but avoid buffering huge bodies.
            try:
                snippet = await resp.content.read(512)
            except Exception:
                snippet = b""
            resp.release()
            raise TTSAPIError(
                f"TikTok API HTTP {resp.status} ({snippet[:128]!r})",
                voice_id,
                resp.status,
            )

        async def producer() -> None:
            try:
                await _decode_tiktok_json_base64_stream(resp, voice_id=voice_id, stream=stream)
                mark_voice_success(voice_id)
            except Exception:
                # Count stream/decode errors against this voice.
                mark_voice_failed(voice_id)
                raise
            finally:
                stream.close()
                resp.release()
                await session.close()

        return asyncio.create_task(producer())
    except Exception:
        if resp is not None:
            resp.release()
        await session.close()
        raise


async def get_tts_stream(
    text: str,
    voice_id: str,
    *,
    fallback_voice: str = FALLBACK_VOICE,
) -> Tuple[QueueStream, asyncio.Task]:
    stream = QueueStream(queue=queue.Queue(), buffer=bytearray())

    requested_voice = voice_id or fallback_voice
    if not is_voice_available(requested_voice):
        requested_voice = fallback_voice

    requested_is_google = is_google_voice(requested_voice)
    breaker = circuit_breakers["google"] if requested_is_google else circuit_breakers["tiktok"]

    async def start(_: int) -> asyncio.Task:
        if requested_is_google:
            return await _open_google_stream(text, requested_voice, stream)
        return await _open_tiktok_stream(text, requested_voice, stream)

    try:
        producer_task = await breaker.execute(lambda: retry_with_backoff(start))
        return stream, producer_task
    except Exception as primary_error:
        if not requested_is_google:
            try:
                producer_task = await circuit_breakers["google"].execute(
                    lambda: _open_google_stream(text, "google_translate", stream)
                )
                return stream, producer_task
            except Exception:
                pass

        if not requested_is_google and requested_voice != fallback_voice:
            try:
                mark_voice_failed(requested_voice)
                producer_task = await retry_with_backoff(
                    lambda _: _open_tiktok_stream(text, fallback_voice, stream)
                )
                return stream, producer_task
            except Exception:
                pass

        stream.close()
        raise primary_error
