import os
from pathlib import Path
from typing import Optional

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        Path(os.path.dirname(self.path) or ".").mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA busy_timeout=5000;")
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discord_users (
                discord_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                nickname TEXT NULL,
                voice_id TEXT NULL,
                updated_at INTEGER NOT NULL
            );
            """
        )
        await self._ensure_user_columns()
        await self._migrate_from_user_voices()
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def _table_exists(self, table_name: str) -> bool:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        async with self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1;", (table_name,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def _migrate_from_user_voices(self) -> None:
        # Older schema used `user_voices(discord_id, voice_id, updated_at)`.
        # Migrate into `discord_users` while keeping the old table intact.
        if self._conn is None:
            raise RuntimeError("Database not connected")
        if not await self._table_exists("user_voices"):
            return

        await self._conn.execute(
            """
            INSERT INTO discord_users(discord_id, display_name, voice_id, updated_at)
            SELECT discord_id, '' AS display_name, voice_id, updated_at
            FROM user_voices
            ON CONFLICT(discord_id) DO UPDATE SET
                voice_id=excluded.voice_id,
                updated_at=excluded.updated_at;
            """
        )

    async def _ensure_user_columns(self) -> None:
        """Ensure schema is upgraded in-place for existing DBs."""

        if self._conn is None:
            raise RuntimeError("Database not connected")

        async with self._conn.execute("PRAGMA table_info(discord_users);") as cursor:
            cols = {row[1] async for row in cursor}

        # Add `nickname` if upgrading from an older schema.
        if "nickname" not in cols:
            await self._conn.execute("ALTER TABLE discord_users ADD COLUMN nickname TEXT NULL;")

    async def upsert_user(self, discord_id: int, display_name: str, updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        await self._conn.execute(
            """
            INSERT INTO discord_users(discord_id, display_name, voice_id, updated_at)
            VALUES(?, ?, NULL, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name=excluded.display_name,
                updated_at=excluded.updated_at;
            """,
            (discord_id, display_name, updated_at),
        )
        await self._conn.commit()

    async def get_user_voice(self, discord_id: int) -> Optional[str]:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        async with self._conn.execute(
            "SELECT voice_id FROM discord_users WHERE discord_id = ?", (discord_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            voice_id = row[0]
            return str(voice_id) if voice_id else None

    async def get_user_nickname(self, discord_id: int) -> Optional[str]:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        async with self._conn.execute(
            "SELECT nickname FROM discord_users WHERE discord_id = ?", (discord_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            nickname = row[0]
            nickname = str(nickname) if nickname else None
            if nickname:
                nickname = nickname.strip()
            return nickname or None

    async def set_user_voice(self, discord_id: int, display_name: str, voice_id: str, updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        await self._conn.execute(
            """
            INSERT INTO discord_users(discord_id, display_name, voice_id, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name=excluded.display_name,
                voice_id=excluded.voice_id,
                updated_at=excluded.updated_at;
            """,
            (discord_id, display_name, voice_id, updated_at),
        )
        await self._conn.commit()

    async def set_user_nickname(self, discord_id: int, display_name: str, nickname: str, updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        await self._conn.execute(
            """
            INSERT INTO discord_users(discord_id, display_name, nickname, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name=excluded.display_name,
                nickname=excluded.nickname,
                updated_at=excluded.updated_at;
            """,
            (discord_id, display_name, nickname, updated_at),
        )
        await self._conn.commit()

    async def delete_user_voice(self, discord_id: int, updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        await self._conn.execute(
            "UPDATE discord_users SET voice_id = NULL, updated_at = ? WHERE discord_id = ?",
            (updated_at, discord_id),
        )
        await self._conn.commit()

    async def delete_user_nickname(self, discord_id: int, updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        await self._conn.execute(
            "UPDATE discord_users SET nickname = NULL, updated_at = ? WHERE discord_id = ?",
            (updated_at, discord_id),
        )
        await self._conn.commit()
