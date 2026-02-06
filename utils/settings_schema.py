import json
from typing import Any, Dict, Mapping

from .config import FALLBACK_VOICE, MAX_TTS_CHARS


class SettingsValidationError(ValueError):
    pass


DEFAULT_SETTINGS: Dict[str, Any] = {
    "max_tts_chars": MAX_TTS_CHARS,
    "fallback_voice": FALLBACK_VOICE,
    "default_voice_id": FALLBACK_VOICE,
    "auto_read_messages": True,
    "leave_when_alone": True,
    "greet_on_join": False,
    "farewell_on_leave": False,
    "restrict_voices": False,
    "allowed_voice_ids": [],
}


def validate_settings(data: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)

    cleaned: Dict[str, Any] = {}

    max_tts_chars = merged.get("max_tts_chars", MAX_TTS_CHARS)
    try:
        max_tts_chars = int(max_tts_chars)
    except (TypeError, ValueError):
        raise SettingsValidationError("max_tts_chars must be an integer")
    if max_tts_chars < 1 or max_tts_chars > 2000:
        raise SettingsValidationError("max_tts_chars must be between 1 and 2000")
    cleaned["max_tts_chars"] = max_tts_chars

    fallback_voice = str(merged.get("fallback_voice", FALLBACK_VOICE)).strip()
    if not fallback_voice:
        raise SettingsValidationError("fallback_voice must be a non-empty string")
    cleaned["fallback_voice"] = fallback_voice

    default_voice_id = str(merged.get("default_voice_id", fallback_voice)).strip()
    if not default_voice_id:
        raise SettingsValidationError("default_voice_id must be a non-empty string")
    cleaned["default_voice_id"] = default_voice_id

    cleaned["auto_read_messages"] = bool(merged.get("auto_read_messages", True))
    cleaned["leave_when_alone"] = bool(merged.get("leave_when_alone", True))

    greet_on_join = merged.get("greet_on_join", False)
    if isinstance(greet_on_join, str):
        greet_on_join = greet_on_join.strip().lower() in {"1", "true", "yes", "y", "on"}
    cleaned["greet_on_join"] = bool(greet_on_join)

    farewell_on_leave = merged.get("farewell_on_leave", False)
    if isinstance(farewell_on_leave, str):
        farewell_on_leave = farewell_on_leave.strip().lower() in {"1", "true", "yes", "y", "on"}
    cleaned["farewell_on_leave"] = bool(farewell_on_leave)

    restrict_voices = merged.get("restrict_voices", False)
    if isinstance(restrict_voices, str):
        restrict_voices = restrict_voices.strip().lower() in {"1", "true", "yes", "y", "on"}
    cleaned["restrict_voices"] = bool(restrict_voices)

    allowed_voice_ids = merged.get("allowed_voice_ids", [])
    if isinstance(allowed_voice_ids, str):
        try:
            allowed_voice_ids = json.loads(allowed_voice_ids)
        except json.JSONDecodeError as exc:
            raise SettingsValidationError("allowed_voice_ids must be a JSON list") from exc
    if allowed_voice_ids is None:
        allowed_voice_ids = []
    if not isinstance(allowed_voice_ids, (list, tuple, set)):
        raise SettingsValidationError("allowed_voice_ids must be a list of strings")

    seen: set[str] = set()
    cleaned_allowed: list[str] = []
    for item in allowed_voice_ids:
        voice = str(item or "").strip()
        if not voice or voice in seen:
            continue
        seen.add(voice)
        cleaned_allowed.append(voice)
        if len(cleaned_allowed) > 500:
            raise SettingsValidationError("allowed_voice_ids is too large (max 500)")

    cleaned["allowed_voice_ids"] = cleaned_allowed

    if cleaned["restrict_voices"]:
        if not cleaned_allowed:
            raise SettingsValidationError("Pick at least one allowed voice (allowed_voice_ids)")
        if fallback_voice not in cleaned_allowed:
            raise SettingsValidationError("fallback_voice must be included in allowed_voice_ids when restrict_voices is enabled")
        if default_voice_id not in cleaned_allowed:
            raise SettingsValidationError(
                "default_voice_id must be included in allowed_voice_ids when restrict_voices is enabled"
            )

    return cleaned
