import re
from typing import Iterable

import discord


def _safe_space(text: str) -> str:
    return " ".join((text or "").split())


def _iter_tokens(prefix: str, ids: Iterable[int]) -> list[str]:
    return [f"<{prefix}{mid}>" for mid in ids]


def normalize_mentions(message: discord.Message) -> str:
    text = message.content or ""

    replacements: dict[str, str] = {}

    for member in message.mentions:
        name = member.display_name if isinstance(member, discord.Member) else getattr(member, "name", str(member.id))
        replacements[f"<@{member.id}>"] = f"@{name}"
        replacements[f"<@!{member.id}>"] = f"@{name}"

    for role in message.role_mentions:
        replacements[f"<@&{role.id}>"] = f"@{role.name}"

    for channel in message.channel_mentions:
        replacements[f"<#{channel.id}>"] = f"#{channel.name}"

    for token, repl in replacements.items():
        if token in text:
            text = text.replace(token, repl)

    # Remove any leftover mention markup.
    text = re.sub(r"<@!?\d+>", "", text)
    text = re.sub(r"<@&\d+>", "", text)
    text = re.sub(r"<#\d+>", "", text)
    return _safe_space(text)
