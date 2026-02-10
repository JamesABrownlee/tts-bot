"""
utils/open_ai.py

DJ intro generator using OpenAI (openai==2.x)
- Uses Responses API
- Uses Structured Outputs via `text={"format": {...}}` (NOT response_format)
- Validates title+artist presence
- Retries once, then falls back

Env var required:
  OPENAI_API_KEY=...

Optional:
  OPENAI_MODEL=gpt-4o-mini
"""

from __future__ import annotations

import json
import os
from typing import Optional, Tuple, Union

from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

SYSTEM = (
    "You are Vexo FM, a charismatic radio host introducing songs.\n"
    "Rules:\n"
    "- intro: 1–2 sentences, max 35 words.\n"
    "- intro MUST include the exact song title and artist provided.\n"
    "- If for_user is provided, dedicate to them; else if requested_by is provided, dedicate to them.\n"
    "- No lyrics. No profanity.\n"
    "- Return ONLY JSON in the required schema.\n"
)

JSON_SCHEMA = {
    "name": "dj_intro",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"intro": {"type": "string"}},
        "required": ["intro"],
    },
    "strict": True,
}


def _has_title_artist(text: str, title: str, artist: str) -> bool:
    t = (title or "").strip().lower()
    a = (artist or "").strip().lower()
    x = (text or "").strip().lower()
    return bool(t) and bool(a) and (t in x) and (a in x)


def dj_intro_fallback(
    *,
    title: str,
    artist: str,
    requested_by: Optional[str] = None,
    for_user: Optional[str] = None,
) -> str:
    who = (for_user or requested_by or "").strip()
    if who:
        return f"Alright {who}, this one’s for you — “{title}” by {artist}, right here on Vexo FM."
    return f"Up next on Vexo FM: “{title}” by {artist}."


def dj_intro(
    *,
    title: str,
    artist: str,
    requested_by: Optional[str] = None,
    for_user: Optional[str] = None,
    return_debug: bool = False,
) -> Union[str, Tuple[str, str, bool]]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        fb = dj_intro_fallback(title=title, artist=artist, requested_by=requested_by, for_user=for_user)
        return (fb, "", True) if return_debug else fb

    client = OpenAI(api_key=api_key)

    payload = {
        "title": title,
        "artist": artist,
        "requested_by": requested_by,
        "for_user": for_user,
    }

    last_raw = ""
    user_content = (
        "Generate the DJ intro JSON for this payload.\n"
        f"Payload:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    for _ in range(2):  # 1 retry
        if hasattr(client, "responses"):
            resp = client.responses.create(
                model=MODEL,
                input=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                # Structured output for Responses API
                text={
                    "format": {
                        "type": "json_schema",
                        "name": JSON_SCHEMA["name"],
                        "schema": JSON_SCHEMA["schema"],
                        "strict": JSON_SCHEMA["strict"],
                    }
                },
                temperature=0.7,
                max_output_tokens=180,
            )
            raw = (resp.output_text or "").strip()
        else:
            # Older OpenAI SDK: fall back to chat completions.
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.7,
                max_tokens=180,
            )
            raw = (resp.choices[0].message.content or "").strip()

        last_raw = raw
        if not raw:
            continue

        try:
            data = json.loads(raw)
            intro = str(data.get("intro", "")).strip()
        except Exception:
            continue

        if not intro:
            continue

        # Guarantee it says title + artist (or fallback)
        if not _has_title_artist(intro, title, artist):
            intro = dj_intro_fallback(title=title, artist=artist, requested_by=requested_by, for_user=for_user)
            return (intro, raw, True) if return_debug else intro

        return (intro, raw, False) if return_debug else intro

    fb = dj_intro_fallback(title=title, artist=artist, requested_by=requested_by, for_user=for_user)
    return (fb, last_raw, True) if return_debug else fb
