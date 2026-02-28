import asyncio

from utils.queue_utils import enqueue_with_drop
from utils.tts_playback import wait_for_playback
from utils.tts_text import normalize_mentions


class DummyUser:
    def __init__(self, user_id: int, display_name: str, name: str | None = None) -> None:
        self.id = user_id
        self.display_name = display_name
        self.name = name or display_name


class DummyRole:
    def __init__(self, role_id: int, name: str) -> None:
        self.id = role_id
        self.name = name


class DummyChannel:
    def __init__(self, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name


class DummyMessage:
    def __init__(self, content: str, mentions, role_mentions, channel_mentions) -> None:
        self.content = content
        self.mentions = mentions
        self.role_mentions = role_mentions
        self.channel_mentions = channel_mentions


async def test_normalize_mentions() -> None:
    user = DummyUser(123, "Alice")
    role = DummyRole(55, "Admins")
    channel = DummyChannel(99, "general")
    msg = DummyMessage(
        "Hello <@123> <@!123> <@&55> <#99> end",
        mentions=[user],
        role_mentions=[role],
        channel_mentions=[channel],
    )
    out = normalize_mentions(msg)
    assert "@Alice" in out
    assert "@Admins" in out
    assert "#general" in out


async def test_queue_drop() -> None:
    q = asyncio.Queue(maxsize=2)
    await q.put("a")
    await q.put("b")
    dropped, ok = await enqueue_with_drop(q, "c", policy="drop_oldest")
    assert ok is True
    assert dropped == 1
    assert q.qsize() == 2


async def test_wait_for_playback() -> None:
    done = asyncio.Event()
    ok = await wait_for_playback(done, timeout=0.1)
    assert ok is False
    done.set()
    ok2 = await wait_for_playback(done, timeout=0.1)
    assert ok2 is True


async def main() -> None:
    await test_normalize_mentions()
    await test_queue_drop()
    await test_wait_for_playback()
    print("Sanity harness passed")


if __name__ == "__main__":
    asyncio.run(main())
