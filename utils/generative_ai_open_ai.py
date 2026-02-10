# utils/open_ai_tts.py
# Provides connectivity to Open AI for generating text-to-speech scripts.
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar, Union

from openai import OpenAI

T = TypeVar("T")

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


@dataclass(frozen=True)
class GenerationConfig:
    model: str = DEFAULT_MODEL
    temperature: float = 0.7
    max_output_tokens: int = 300
    retries: int = 1  # number of retries after first attempt


def build_text_json_schema_format(*, name: str, schema: Dict[str, Any], strict: bool = True) -> Dict[str, Any]:
    """
    Responses API structured output format container (text.format).
    See Structured Outputs guide. :contentReference[oaicite:1]{index=1}
    """
    return {
        "type": "json_schema",
        "name": name,
        "schema": schema,
        "strict": strict,
    }


def generate_structured(
    *,
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    schema: Dict[str, Any],
    schema_strict: bool = True,
    payload: Optional[Dict[str, Any]] = None,
    config: GenerationConfig = GenerationConfig(),
    api_key_env: str = "OPENAI_API_KEY",
    validate: Optional[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = None,
    fallback: Optional[Callable[[Dict[str, Any]], T]] = None,
    return_debug: bool = False,
) -> Union[T, Tuple[T, str, bool]]:
    """
    Generic JSON-schema-constrained generator for TTS scripts (or anything else).

    - Uses Responses API (`client.responses.create`) :contentReference[oaicite:2]{index=2}
    - Uses Structured Outputs via `text={"format": ...}` :contentReference[oaicite:3]{index=3}
    - Custom validation + fallback
    - Retries once by default
    """
    api_key = os.environ.get(api_key_env)
    payload = payload or {}

    def _fallback_result(raw: str) -> Union[T, Tuple[T, str, bool]]:
        if fallback is None:
            raise RuntimeError("Generation failed and no fallback was provided.")
        fb_val = fallback(payload)
        return (fb_val, raw, True) if return_debug else fb_val

    if not api_key:
        return _fallback_result("")

    client = OpenAI(api_key=api_key)

    # Construct user content. Keep it predictable: prompt + payload JSON.
    content = user_prompt
    if payload:
        content += "\n\nPayload:\n" + json.dumps(payload, ensure_ascii=False)

    last_raw = ""
    attempts = 1 + max(0, config.retries)

    for _ in range(attempts):
        resp = client.responses.create(
            model=config.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            text={"format": build_text_json_schema_format(name=schema_name, schema=schema, strict=schema_strict)},
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
        )

        raw = (resp.output_text or "").strip()
        last_raw = raw
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        # Optional user validation (e.g., ensure required phrases, length, etc.)
        if validate is not None:
            try:
                ok = bool(validate(data, payload))
            except Exception:
                ok = False
            if not ok:
                continue

        # If you want this function to be fully generic, return the parsed object.
        # But for TTS, you usually want a string field like "text" or "ssml".
        # We'll return data as-is; caller decides how to extract/convert.
        return (data, raw, False) if return_debug else data  # type: ignore[return-value]

    return _fallback_result(last_raw)