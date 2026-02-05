import asyncio
import json
import os
from typing import Any, Dict, Optional

from .config import FALLBACK_VOICE, MAX_TTS_CHARS

DEFAULT_SETTINGS: Dict[str, Any] = {
    "max_tts_chars": MAX_TTS_CHARS,
    "fallback_voice": FALLBACK_VOICE,
    "default_voice_id": FALLBACK_VOICE,
    "auto_read_messages": True,
    "leave_when_alone": True,
}


class SettingsValidationError(ValueError):
    pass


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
                self._data = self._validate(merged)
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
            merged = self._validate(merged)
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

    def _validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        cleaned: Dict[str, Any] = {}

        max_tts_chars = data.get("max_tts_chars", MAX_TTS_CHARS)
        try:
            max_tts_chars = int(max_tts_chars)
        except (TypeError, ValueError):
            raise SettingsValidationError("max_tts_chars must be an integer")
        if max_tts_chars < 1 or max_tts_chars > 2000:
            raise SettingsValidationError("max_tts_chars must be between 1 and 2000")
        cleaned["max_tts_chars"] = max_tts_chars

        fallback_voice = str(data.get("fallback_voice", FALLBACK_VOICE)).strip()
        if not fallback_voice:
            raise SettingsValidationError("fallback_voice must be a non-empty string")
        cleaned["fallback_voice"] = fallback_voice

        default_voice_id = str(data.get("default_voice_id", fallback_voice)).strip()
        if not default_voice_id:
            raise SettingsValidationError("default_voice_id must be a non-empty string")
        cleaned["default_voice_id"] = default_voice_id

        cleaned["auto_read_messages"] = bool(data.get("auto_read_messages", True))
        cleaned["leave_when_alone"] = bool(data.get("leave_when_alone", True))

        return cleaned
