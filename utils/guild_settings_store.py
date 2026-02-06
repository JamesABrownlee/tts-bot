import asyncio
import time
from typing import Any, Dict, Iterable, Optional

from .db import Database
from .settings_schema import DEFAULT_SETTINGS, SettingsValidationError, validate_settings


class GuildSettingsStore:
    """Cache + DB-backed settings per Discord guild."""

    def __init__(self, db: Database, *, defaults: Optional[Dict[str, Any]] = None) -> None:
        self._db = db
        self._defaults = validate_settings(defaults or DEFAULT_SETTINGS)
        self._cache: dict[int, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def _get_locked(self, guild_id: int) -> Dict[str, Any]:
        cached = self._cache.get(guild_id)
        if cached is not None:
            return dict(cached)

        settings = await self._db.get_guild_settings(guild_id)
        if settings is None:
            settings = dict(self._defaults)
            await self._db.ensure_guild_settings(guild_id, settings, int(time.time()))
        else:
            settings = validate_settings(settings)

        self._cache[guild_id] = dict(settings)
        return dict(settings)

    async def preload(self, guild_ids: Iterable[int]) -> None:
        async with self._lock:
            for gid in guild_ids:
                await self._get_locked(int(gid))

    async def get(self, guild_id: int) -> Dict[str, Any]:
        async with self._lock:
            return await self._get_locked(guild_id)

    async def update(self, guild_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            current = await self._get_locked(guild_id)
            merged = dict(current)
            for k, v in patch.items():
                if k not in DEFAULT_SETTINGS:
                    raise SettingsValidationError(f"Unknown setting: {k}")
                merged[k] = v

            cleaned = validate_settings(merged)
            await self._db.upsert_guild_settings(guild_id, cleaned, int(time.time()))
            self._cache[guild_id] = dict(cleaned)
            return dict(cleaned)

    async def invalidate(self, guild_id: int) -> None:
        async with self._lock:
            self._cache.pop(guild_id, None)

