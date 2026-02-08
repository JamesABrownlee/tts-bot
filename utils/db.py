import json
import os
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from .settings_schema import DEFAULT_SETTINGS, validate_settings


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
                auto_join INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                max_tts_chars INTEGER NOT NULL,
                fallback_voice TEXT NOT NULL,
                default_voice_id TEXT NOT NULL,
                auto_read_messages INTEGER NOT NULL,
                leave_when_alone INTEGER NOT NULL,
                greet_on_join INTEGER NOT NULL DEFAULT 0,
                farewell_on_leave INTEGER NOT NULL DEFAULT 0,
                restrict_voices INTEGER NOT NULL DEFAULT 0,
                allowed_voice_ids TEXT NOT NULL DEFAULT '[]',
                updated_at INTEGER NOT NULL
            );
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS member_seen (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_seen_date TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(guild_id, user_id)
            );
            """
        )
        await self._ensure_user_columns()
        await self._ensure_guild_settings_columns()
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
        if "auto_join" not in cols:
            await self._conn.execute(
                "ALTER TABLE discord_users ADD COLUMN auto_join INTEGER NOT NULL DEFAULT 0;"
            )

    async def _ensure_guild_settings_columns(self) -> None:
        """Ensure guild settings schema is upgraded in-place for existing DBs."""

        if self._conn is None:
            raise RuntimeError("Database not connected")

        async with self._conn.execute("PRAGMA table_info(guild_settings);") as cursor:
            cols = {row[1] async for row in cursor}

        if "greet_on_join" not in cols:
            await self._conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN greet_on_join INTEGER NOT NULL DEFAULT 0;"
            )
        if "farewell_on_leave" not in cols:
            await self._conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN farewell_on_leave INTEGER NOT NULL DEFAULT 0;"
            )
        if "restrict_voices" not in cols:
            await self._conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN restrict_voices INTEGER NOT NULL DEFAULT 0;"
            )
        if "allowed_voice_ids" not in cols:
            await self._conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN allowed_voice_ids TEXT NOT NULL DEFAULT '[]';"
            )

    async def get_member_last_seen(self, guild_id: int, user_id: int) -> Optional[str]:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        async with self._conn.execute(
            "SELECT last_seen_date FROM member_seen WHERE guild_id = ? AND user_id = ?;",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def upsert_member_last_seen(self, guild_id: int, user_id: int, last_seen_date: str, updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        await self._conn.execute(
            """
            INSERT INTO member_seen(guild_id, user_id, last_seen_date, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                last_seen_date=excluded.last_seen_date,
                updated_at=excluded.updated_at;
            """,
            (guild_id, user_id, last_seen_date, updated_at),
        )
        await self._conn.commit()

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

    async def get_user_auto_join(self, discord_id: int) -> bool:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        async with self._conn.execute(
            "SELECT auto_join FROM discord_users WHERE discord_id = ?", (discord_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0])

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

    async def set_user_auto_join(self, discord_id: int, display_name: str, auto_join: bool, updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        await self._conn.execute(
            """
            INSERT INTO discord_users(discord_id, display_name, auto_join, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name=excluded.display_name,
                auto_join=excluded.auto_join,
                updated_at=excluded.updated_at;
            """,
            (discord_id, display_name, 1 if auto_join else 0, updated_at),
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

    async def replace_user_voice(self, from_voice_id: str, to_voice_id: str, updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        await self._conn.execute(
            "UPDATE discord_users SET voice_id = ?, updated_at = ? WHERE voice_id = ?",
            (to_voice_id, updated_at, from_voice_id),
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

    async def get_guild_settings(self, guild_id: int) -> Optional[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        async with self._conn.execute(
            """
            SELECT
                max_tts_chars, fallback_voice, default_voice_id,
                auto_read_messages, leave_when_alone,
                greet_on_join, farewell_on_leave,
                restrict_voices, allowed_voice_ids
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            allowed_raw = row[8] or "[]"
            allowed: Any
            if isinstance(allowed_raw, (bytes, bytearray)):
                allowed_raw = allowed_raw.decode("utf-8", errors="replace")
            try:
                allowed = json.loads(str(allowed_raw))
            except Exception:
                allowed = []

            raw = {
                "max_tts_chars": row[0],
                "fallback_voice": row[1],
                "default_voice_id": row[2],
                "auto_read_messages": bool(row[3]),
                "leave_when_alone": bool(row[4]),
                "greet_on_join": bool(row[5]),
                "farewell_on_leave": bool(row[6]),
                "restrict_voices": bool(row[7]),
                "allowed_voice_ids": allowed,
            }
            return validate_settings(raw)

    async def ensure_guild_settings(self, guild_id: int, settings: dict[str, Any], updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        cleaned = validate_settings({**DEFAULT_SETTINGS, **settings})
        await self._conn.execute(
            """
            INSERT INTO guild_settings(
                guild_id, max_tts_chars, fallback_voice, default_voice_id,
                auto_read_messages, leave_when_alone,
                greet_on_join, farewell_on_leave,
                restrict_voices, allowed_voice_ids,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO NOTHING;
            """,
            (
                guild_id,
                int(cleaned["max_tts_chars"]),
                str(cleaned["fallback_voice"]),
                str(cleaned["default_voice_id"]),
                1 if cleaned["auto_read_messages"] else 0,
                1 if cleaned["leave_when_alone"] else 0,
                1 if cleaned["greet_on_join"] else 0,
                1 if cleaned["farewell_on_leave"] else 0,
                1 if cleaned["restrict_voices"] else 0,
                json.dumps(cleaned.get("allowed_voice_ids") or []),
                updated_at,
            ),
        )
        await self._conn.commit()

    async def upsert_guild_settings(self, guild_id: int, settings: dict[str, Any], updated_at: int) -> None:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        cleaned = validate_settings({**DEFAULT_SETTINGS, **settings})
        await self._conn.execute(
            """
            INSERT INTO guild_settings(
                guild_id, max_tts_chars, fallback_voice, default_voice_id,
                auto_read_messages, leave_when_alone,
                greet_on_join, farewell_on_leave,
                restrict_voices, allowed_voice_ids,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                max_tts_chars=excluded.max_tts_chars,
                fallback_voice=excluded.fallback_voice,
                default_voice_id=excluded.default_voice_id,
                auto_read_messages=excluded.auto_read_messages,
                leave_when_alone=excluded.leave_when_alone,
                greet_on_join=excluded.greet_on_join,
                farewell_on_leave=excluded.farewell_on_leave,
                restrict_voices=excluded.restrict_voices,
                allowed_voice_ids=excluded.allowed_voice_ids,
                updated_at=excluded.updated_at;
            """,
            (
                guild_id,
                int(cleaned["max_tts_chars"]),
                str(cleaned["fallback_voice"]),
                str(cleaned["default_voice_id"]),
                1 if cleaned["auto_read_messages"] else 0,
                1 if cleaned["leave_when_alone"] else 0,
                1 if cleaned["greet_on_join"] else 0,
                1 if cleaned["farewell_on_leave"] else 0,
                1 if cleaned["restrict_voices"] else 0,
                json.dumps(cleaned.get("allowed_voice_ids") or []),
                updated_at,
            ),
        )
        await self._conn.commit()
