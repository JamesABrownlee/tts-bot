import asyncio
import json
import os
from typing import Any, Dict, Optional

from .settings_schema import DEFAULT_SETTINGS, SettingsValidationError, validate_settings

VERSION = "1.1.0"

class SettingsStore:
    def __init__(self, path: str, defaults: Optional[Dict[str, Any]] = None) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = dict(defaults or DEFAULT_SETTINGS)

    async def load(self) -> Dict[str, Any]:
        async with self._lock:
            data = await asyncio.to_thread(self._read_file)
            if data is None:
                await asyncio.to_thread(self._write_file, self._data)
            else:
                # Merge and validate
                merged = dict(DEFAULT_SETTINGS)
                merged.update(data)
                self._data = validate_settings(merged)
                await asyncio.to_thread(self._write_file, self._data)
            return dict(self._data)

    async def get(self) -> Dict[str, Any]:
        async with self._lock:
            return dict(self._data)

    async def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            merged = dict(self._data)
            for k, v in patch.items():
                if k not in DEFAULT_SETTINGS:
                    raise SettingsValidationError(f"Unknown setting: {k}")
                merged[k] = v
            merged = validate_settings(merged)
            self._data = merged
            await asyncio.to_thread(self._write_file, self._data)
            return dict(self._data)

    def _read_file(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            # Start fresh if file is corrupt.
            return None

    def _write_file(self, data: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, self.path)
