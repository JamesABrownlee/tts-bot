import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.config import ALL_VOICES, FALLBACK_VOICE, MAX_TTS_CHARS, POPULAR_VOICE_IDS, VOICE_ID_TO_NAME
from utils.logger import get_logger
from utils.tts_pipeline import get_tts_stream

logger = get_logger("tts")


@dataclass(frozen=True)
class QueueItem:
    text: str
    voice_id: str
    volume: Optional[float] = None


@dataclass
class GuildState:
    voice_client: Optional[discord.VoiceClient] = None
    voice_channel_id: Optional[int] = None  # locked voice channel
    last_voice_channel_id: Optional[int] = None  # last successful connection
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker: Optional[asyncio.Task] = None
    connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_speaker_id: Optional[int] = None
    last_connect_attempt: float = 0.0


class VoicePickerView(discord.ui.View):
    def __init__(
        self,
        cog,
        member: discord.Member,
        *,
        voices: list[tuple[str, str]],
        default_voice: str,
        current_voice: str,
        allowed_voice_ids: Optional[set[str]] = None,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.member = member
        self.user_id = member.id
        self.voices = voices or ALL_VOICES
        self.default_voice = default_voice
        self.current_voice = current_voice
        self.allowed_voice_ids = allowed_voice_ids
        self.page = page
        self.per_page = 25
        self._select: Optional[discord.ui.Select] = None
        self._render()

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(self.voices) / self.per_page))

    def _page_items(self) -> list[tuple[str, str]]:
        start = self.page * self.per_page
        end = start + self.per_page
        return self.voices[start:end]

    def _render(self) -> None:
        self.clear_items()

        options: list[discord.SelectOption] = []
        for voice_id, name in self._page_items():
            label = (name or voice_id)[:100]
            description = voice_id[:100]
            options.append(discord.SelectOption(label=label, value=voice_id, description=description))

        placeholder = f"Pick a voice… (page {self.page + 1}/{self.page_count})"
        select = discord.ui.Select(placeholder=placeholder, min_values=1, max_values=1, options=options)
        select.callback = self._on_select  # type: ignore[assignment]
        self._select = select
        self.add_item(select)

        prev_btn = discord.ui.Button(
            label="Prev",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
        )
        prev_btn.callback = self._on_prev  # type: ignore[assignment]

        next_btn = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= (self.page_count - 1),
        )
        next_btn.callback = self._on_next  # type: ignore[assignment]

        reset_btn = discord.ui.Button(label="Reset", style=discord.ButtonStyle.danger)
        reset_btn.callback = self._on_reset  # type: ignore[assignment]

        close_btn = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary)
        close_btn.callback = self._on_close  # type: ignore[assignment]

        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(reset_btn)
        self.add_item(close_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
        return False

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not self._select or not self._select.values:
            await interaction.response.defer()
            return

        voice_id = self._select.values[0]
        if self.allowed_voice_ids is not None and voice_id not in self.allowed_voice_ids:
            await interaction.response.send_message("That voice isn't allowed in this server.", ephemeral=True)
            return
        await self.cog._set_voice_pref(self.member, voice_id)
        self.current_voice = voice_id

        friendly = VOICE_ID_TO_NAME.get(voice_id)
        suffix = f" ({friendly})" if friendly else ""
        await interaction.response.edit_message(content=f"Saved! Your voice is now `{voice_id}`{suffix}.", view=self)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._render()
        await interaction.response.edit_message(view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        self.page = min(self.page_count - 1, self.page + 1)
        self._render()
        await interaction.response.edit_message(view=self)

    async def _on_reset(self, interaction: discord.Interaction) -> None:
        await self.cog._set_voice_pref(self.member, self.default_voice)
        self.current_voice = self.default_voice

        friendly = VOICE_ID_TO_NAME.get(self.default_voice)
        suffix = f" ({friendly})" if friendly else ""
        await interaction.response.edit_message(
            content=f"Reset! Your voice is now `{self.default_voice}`{suffix}.",
            view=self,
        )

    async def _on_close(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            item.disabled = True
        self.stop()
        await interaction.response.edit_message(view=self)


class TTSCog(commands.Cog):
    set_group = app_commands.Group(name="set", description="Set preferences")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.state_by_guild: dict[int, GuildState] = {}
        # Cache `discord_id -> voice_id` (or None if unset) to avoid hitting SQLite on every message.
        self.user_voice_cache: dict[int, Optional[str]] = {}
        # Cache `discord_id -> nickname` (or None if unset).
        self.user_nickname_cache: dict[int, Optional[str]] = {}
        # Cache `discord_id -> auto_join` (or None if unset).
        self.user_auto_join_cache: dict[int, Optional[bool]] = {}
        self._default_voice_by_guild: dict[int, str] = {}
        self._health_task: Optional[asyncio.Task] = None

    def cog_unload(self) -> None:
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._health_task and not self._health_task.done():
            return
        self._health_task = asyncio.create_task(self._voice_health_loop())

    async def _voice_health_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in list(self.bot.guilds):
                    state = self.state_by_guild.get(guild.id)
                    if not state:
                        continue

                    vc = state.voice_client
                    if vc and vc.is_connected() and vc.channel:
                        state.last_voice_channel_id = vc.channel.id
                        continue

                    guild_vc = guild.voice_client
                    if guild_vc and guild_vc.is_connected() and guild_vc.channel:
                        state.voice_client = guild_vc
                        state.last_voice_channel_id = guild_vc.channel.id
                        continue

                    target_id = state.voice_channel_id or state.last_voice_channel_id
                    if not target_id:
                        continue

                    channel = guild.get_channel(target_id)
                    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                        continue

                    non_bot_members = [m for m in channel.members if not m.bot]
                    if not non_bot_members:
                        continue

                    ok = await self.ensure_connected(guild, channel)
                    if ok:
                        logger.info(
                            "Voice health: reconnected guild=%s channel=%s",
                            guild.id,
                            channel.id,
                        )
            except Exception as exc:
                logger.warning("Voice health loop error: %s", exc)

            await asyncio.sleep(20)

    async def get_settings(self, guild_id: Optional[int] = None) -> dict:
        store = getattr(self.bot, "guild_settings", None)
        if store is not None and guild_id is not None:
            try:
                settings = await store.get(guild_id)
                await self._maybe_migrate_default_voice(guild_id, settings)
                return settings
            except Exception as exc:
                logger.warning("Failed to read guild settings: guild=%s err=%s", guild_id, exc)

        store = getattr(self.bot, "settings", None)
        if store is None:
            return {
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
        return await store.get()

    def _bot_default_voice(self, settings: dict) -> str:
        return str(settings.get("default_voice_id") or FALLBACK_VOICE).strip() or FALLBACK_VOICE

    def _user_default_voice(self, settings: dict) -> str:
        default_voice = self._bot_default_voice(settings)
        fallback_voice = str(settings.get("fallback_voice") or FALLBACK_VOICE).strip() or FALLBACK_VOICE
        if fallback_voice and fallback_voice != default_voice:
            return fallback_voice
        for vid, _name in ALL_VOICES:
            if vid != default_voice:
                return vid
        return default_voice

    async def _maybe_migrate_default_voice(self, guild_id: int, settings: dict) -> None:
        new_default = self._bot_default_voice(settings)
        old_default = self._default_voice_by_guild.get(guild_id)
        replacement = self._user_default_voice(settings)

        async def migrate_voice(voice_id: Optional[str]) -> None:
            if not voice_id:
                return
            db = getattr(self.bot, "db", None)
            if db is not None:
                try:
                    await db.replace_user_voice(voice_id, replacement, int(time.time()))
                except Exception as exc:
                    logger.warning(
                        "Failed to migrate user voices: old=%s new=%s err=%s",
                        voice_id,
                        replacement,
                        exc,
                    )
            for uid, vid in list(self.user_voice_cache.items()):
                if vid == voice_id:
                    self.user_voice_cache[uid] = replacement

        if old_default != new_default:
            if old_default:
                await migrate_voice(old_default)
            await migrate_voice(new_default)
        elif old_default is None:
            await migrate_voice(new_default)

        self._default_voice_by_guild[guild_id] = new_default

    def _allowed_voice_ids(self, settings: dict) -> Optional[set[str]]:
        if not settings.get("restrict_voices"):
            return None
        raw = settings.get("allowed_voice_ids") or []
        if not isinstance(raw, (list, tuple, set)):
            return set()
        allowed: set[str] = set()
        for item in raw:
            voice_id = str(item or "").strip()
            if voice_id:
                allowed.add(voice_id)
        return allowed

    def _is_voice_allowed(self, settings: dict, voice_id: str) -> bool:
        allowed = self._allowed_voice_ids(settings)
        if allowed is None:
            return True
        return voice_id in allowed

    def _effective_voice_id(
        self, settings: dict, requested_voice_id: Optional[str], *, allow_default: bool = True
    ) -> str:
        default_voice = self._bot_default_voice(settings)
        fallback_voice = str(settings.get("fallback_voice") or FALLBACK_VOICE).strip() or FALLBACK_VOICE

        if requested_voice_id:
            voice_id = str(requested_voice_id).strip()
        else:
            voice_id = default_voice if allow_default else self._user_default_voice(settings)

        if not allow_default and voice_id == default_voice:
            voice_id = self._user_default_voice(settings)

        allowed = self._allowed_voice_ids(settings)
        if allowed is None:
            return voice_id
        if voice_id in allowed:
            return voice_id
        if allow_default:
            if default_voice in allowed:
                return default_voice
            if fallback_voice in allowed:
                return fallback_voice
            return voice_id

        user_default = self._user_default_voice(settings)
        if user_default in allowed:
            return user_default
        for vid in allowed:
            if vid != default_voice:
                return vid
        if default_voice in allowed:
            return default_voice
        return voice_id

    def _voice_items_for_settings(self, settings: dict, *, exclude_voice_ids: Optional[set[str]] = None) -> list[tuple[str, str]]:
        allowed = self._allowed_voice_ids(settings)
        excluded = exclude_voice_ids or set()
        if allowed is None:
            return [(vid, name) for vid, name in ALL_VOICES if vid not in excluded]
        allowed_list = settings.get("allowed_voice_ids") or []
        if not isinstance(allowed_list, list):
            allowed_list = list(allowed)

        items: list[tuple[str, str]] = [
            (vid, name) for vid, name in ALL_VOICES if vid in allowed and vid not in excluded
        ]
        known_ids = {vid for vid, _name in items}
        for vid in allowed_list:
            vid = str(vid or "").strip()
            if not vid or vid in known_ids or vid in excluded:
                continue
            items.append((vid, VOICE_ID_TO_NAME.get(vid, vid)))
            known_ids.add(vid)

        return items or [(vid, name) for vid, name in ALL_VOICES if vid not in excluded]

    def get_state(self, guild_id: int) -> GuildState:
        state = self.state_by_guild.get(guild_id)
        if not state:
            state = GuildState()
            self.state_by_guild[guild_id] = state
        return state

    async def ensure_worker(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if state.worker and not state.worker.done():
            return

        async def worker_loop() -> None:
            while True:
                item = await state.queue.get()
                if item is None:
                    return
                try:
                    if not state.voice_client or not state.voice_client.is_connected():
                        continue
                    await self.play_tts(
                        guild_id,
                        state.voice_client,
                        item.text,
                        item.voice_id,
                        volume=item.volume,
                    )
                finally:
                    state.queue.task_done()

        state.worker = asyncio.create_task(worker_loop())

    async def stop_worker(self, state: GuildState) -> None:
        if state.worker and not state.worker.done():
            await state.queue.put(None)
            await state.worker

    async def play_tts(
        self,
        guild_id: int,
        voice_client: discord.VoiceClient,
        text: str,
        voice_id: str,
        *,
        volume: Optional[float] = None,
    ) -> None:
        settings = await self.get_settings(guild_id)
        voice_id = self._effective_voice_id(settings, voice_id)
        max_tts_chars = int(settings.get("max_tts_chars", MAX_TTS_CHARS))
        fallback_voice = str(settings.get("fallback_voice", FALLBACK_VOICE))

        clean_text = (text or "").strip()
        if not clean_text:
            return
        if len(clean_text) > max_tts_chars:
            clean_text = clean_text[:max_tts_chars]

        stream, producer_task = await get_tts_stream(clean_text, voice_id, fallback_voice=fallback_voice)
        done = asyncio.Event()

        def after_playback(_err: Optional[Exception]) -> None:
            self.bot.loop.call_soon_threadsafe(done.set)

        source = discord.FFmpegPCMAudio(stream, pipe=True)
        if volume is not None:
            safe_volume = max(0.0, min(2.0, float(volume)))
            source = discord.PCMVolumeTransformer(source, volume=safe_volume)
        voice_client.play(source, after=after_playback)
        await done.wait()

        try:
            await producer_task
        except Exception as exc:
            logger.warning("TTS stream task error: %s", exc)

    def _get_display_name(self, user: discord.abc.User) -> str:
        if isinstance(user, discord.Member):
            return user.display_name
        return getattr(user, "global_name", None) or user.name

    async def _upsert_user_display_name(self, user: discord.abc.User) -> None:
        db = getattr(self.bot, "db", None)
        if db is None:
            return
        try:
            await db.upsert_user(int(user.id), self._get_display_name(user), int(time.time()))
        except Exception as exc:
            logger.warning("DB upsert_user failed: %s", exc)

    async def get_user_voice(self, discord_id: int) -> Optional[str]:
        if discord_id in self.user_voice_cache:
            return self.user_voice_cache[discord_id]

        db = getattr(self.bot, "db", None)
        if db is None:
            self.user_voice_cache[discord_id] = None
            return None

        voice_id = await db.get_user_voice(discord_id)
        self.user_voice_cache[discord_id] = voice_id
        return voice_id

    async def get_user_nickname(self, discord_id: int) -> Optional[str]:
        if discord_id in self.user_nickname_cache:
            return self.user_nickname_cache[discord_id]

        db = getattr(self.bot, "db", None)
        if db is None:
            self.user_nickname_cache[discord_id] = None
            return None

        nickname = await db.get_user_nickname(discord_id)
        self.user_nickname_cache[discord_id] = nickname
        return nickname

    async def get_user_auto_join(self, discord_id: int) -> bool:
        cached = self.user_auto_join_cache.get(discord_id)
        if cached is not None:
            return cached

        db = getattr(self.bot, "db", None)
        if db is None:
            self.user_auto_join_cache[discord_id] = False
            return False

        auto_join = await db.get_user_auto_join(discord_id)
        self.user_auto_join_cache[discord_id] = bool(auto_join)
        return bool(auto_join)

    async def _set_voice_pref(self, member: discord.Member, voice_id: str) -> None:
        db = getattr(self.bot, "db", None)
        if db is None:
            raise RuntimeError("Database is not configured")

        await self._upsert_user_display_name(member)
        await db.set_user_voice(member.id, member.display_name, voice_id, int(time.time()))
        self.user_voice_cache[member.id] = voice_id

    async def _reset_voice_pref(self, member: discord.Member) -> None:
        db = getattr(self.bot, "db", None)
        if db is None:
            raise RuntimeError("Database is not configured")

        await self._upsert_user_display_name(member)
        await db.delete_user_voice(member.id, int(time.time()))
        self.user_voice_cache.pop(member.id, None)

    async def _set_nickname_pref(self, member: discord.Member, nickname: str) -> None:
        db = getattr(self.bot, "db", None)
        if db is None:
            raise RuntimeError("Database is not configured")

        await self._upsert_user_display_name(member)
        await db.set_user_nickname(member.id, member.display_name, nickname, int(time.time()))
        self.user_nickname_cache[member.id] = nickname

    async def _reset_nickname_pref(self, member: discord.Member) -> None:
        db = getattr(self.bot, "db", None)
        if db is None:
            raise RuntimeError("Database is not configured")

        await self._upsert_user_display_name(member)
        await db.delete_user_nickname(member.id, int(time.time()))
        self.user_nickname_cache.pop(member.id, None)

    async def _set_auto_join_pref(self, member: discord.Member, enabled: bool) -> None:
        db = getattr(self.bot, "db", None)
        if db is None:
            raise RuntimeError("Database is not configured")

        await self._upsert_user_display_name(member)
        await db.set_user_auto_join(member.id, member.display_name, enabled, int(time.time()))
        self.user_auto_join_cache[member.id] = bool(enabled)

    async def get_user_speak_name(self, member: discord.Member) -> str:
        nickname = await self.get_user_nickname(member.id)
        if nickname:
            return nickname
        return member.display_name

    async def get_user_announcement_name(self, member: discord.Member) -> str:
        nickname = await self.get_user_nickname(member.id)
        if nickname:
            return nickname
        # Use the Discord username when no custom nickname is saved.
        return member.name

    async def ensure_connected(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
    ) -> bool:
        state = self.get_state(guild.id)

        async with state.connect_lock:
            state_chan = getattr(state.voice_client, "channel", None)
            guild_vc = guild.voice_client
            guild_chan = getattr(guild_vc, "channel", None)
            logger.info(
                "Voice ensure: guild=%s target=%s state_vc=%s state_connected=%s state_chan=%s state_chan_name=%s "
                "guild_vc=%s guild_connected=%s guild_chan=%s guild_chan_name=%s",
                guild.id,
                voice_channel.id,
                bool(state.voice_client),
                bool(state.voice_client and state.voice_client.is_connected()),
                getattr(state_chan, "id", None),
                getattr(state_chan, "name", None),
                bool(guild_vc),
                bool(guild_vc and guild_vc.is_connected()),
                getattr(guild_chan, "id", None),
                getattr(guild_chan, "name", None),
            )
            # Already connected.
            if state.voice_client and state.voice_client.is_connected() and state.voice_client.channel:
                if state.voice_client.channel.id == voice_channel.id:
                    return True

                # Locked to another channel in the guild.
                return False

            # Discord may already have a live voice client not tracked in state.
            if guild.voice_client and guild.voice_client.is_connected() and guild.voice_client.channel:
                if guild.voice_client.channel.id == voice_channel.id:
                    state.voice_client = guild.voice_client
                    state.voice_channel_id = voice_channel.id
                    state.last_voice_channel_id = voice_channel.id
                    return True
                return False

            now = time.time()
            if state.last_connect_attempt and now - state.last_connect_attempt < 5.0:
                logger.info(
                    "Connect cooldown: guild=%s channel=%s wait=%.1fs",
                    guild.id,
                    voice_channel.id,
                    5.0 - (now - state.last_connect_attempt),
                )
                return False
            state.last_connect_attempt = now

            # Connect and lock.
            state.voice_channel_id = voice_channel.id
            state.last_voice_channel_id = voice_channel.id
            state.last_speaker_id = None

            logger.info("Connecting voice: guild=%s channel=%s", guild.id, voice_channel.id)

            try:
                state.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
            except discord.ClientException as exc:
                if "Already connected" in str(exc):
                    logger.warning(
                        "Voice already connected, forcing reconnect: guild=%s channel=%s",
                        guild.id,
                        voice_channel.id,
                    )
                    vc = guild.voice_client
                    if vc and vc.is_connected() and vc.channel:
                        if vc.channel.id != voice_channel.id:
                            try:
                                await vc.move_to(voice_channel)
                            except Exception as exc_move:
                                logger.warning(
                                    "Failed to move voice: guild=%s channel=%s err=%s",
                                    guild.id,
                                    voice_channel.id,
                                    exc_move,
                                )
                                return False
                        state.voice_client = vc
                        state.voice_channel_id = voice_channel.id
                        state.last_voice_channel_id = voice_channel.id
                        return True
                    try:
                        if state.voice_client:
                            await state.voice_client.disconnect()
                    except Exception:
                        pass
                    state.voice_client = None
                    state.voice_channel_id = None
                    await asyncio.sleep(0.5)
                    if guild.voice_client and guild.voice_client.is_connected() and guild.voice_client.channel:
                        if guild.voice_client.channel.id == voice_channel.id:
                            state.voice_client = guild.voice_client
                            state.voice_channel_id = voice_channel.id
                            state.last_voice_channel_id = voice_channel.id
                            return True
                    try:
                        state.voice_client = await voice_channel.connect(self_deaf=True, timeout=20.0)
                    except Exception as exc2:
                        if "Already connected" in str(exc2):
                            if guild.voice_client and guild.voice_client.is_connected() and guild.voice_client.channel:
                                if guild.voice_client.channel.id == voice_channel.id:
                                    state.voice_client = guild.voice_client
                                    state.voice_channel_id = voice_channel.id
                                    state.last_voice_channel_id = voice_channel.id
                                    return True
                        logger.warning(
                            "Failed to reconnect voice: guild=%s channel=%s err=%s",
                            guild.id,
                            voice_channel.id,
                            exc2,
                        )
                        state.voice_client = None
                        state.voice_channel_id = None
                        return False
                    await self.ensure_worker(guild.id)
                    return True
                logger.warning(
                    "Failed to connect voice: guild=%s channel=%s err=%s",
                    guild.id,
                    voice_channel.id,
                    exc,
                )
                state.voice_client = None
                state.voice_channel_id = None
                return False
            except Exception as exc:
                logger.warning(
                    "Failed to connect voice: guild=%s channel=%s err=%s",
                    guild.id,
                    voice_channel.id,
                    exc,
                )
                state.voice_client = None
                state.voice_channel_id = None
                return False

            await self.ensure_worker(guild.id)
            return True

    async def disconnect(self, guild_id: int, reason: str) -> None:
        state = self.get_state(guild_id)
        async with state.connect_lock:
            if state.voice_client and state.voice_client.is_connected():
                logger.info("Disconnecting voice: guild=%s reason=%s", guild_id, reason)
                await state.voice_client.disconnect()
            state.voice_client = None
            state.voice_channel_id = None
            if reason in {"slash_leave", "alone"}:
                state.last_voice_channel_id = None
            state.last_speaker_id = None
            await self.stop_worker(state)

    async def check_should_leave(self, guild: discord.Guild) -> None:
        settings = await self.get_settings(guild.id)
        if not settings.get("leave_when_alone", True):
            return

        state = self.get_state(guild.id)
        vc = state.voice_client
        if not vc or not vc.is_connected() or not vc.channel:
            return

        non_bot_members = [m for m in vc.channel.members if not m.bot]
        if len(non_bot_members) == 0:
            await self.disconnect(guild.id, "alone")

    def _is_voice_chat_text_channel(self, channel: discord.abc.GuildChannel) -> bool:
        t = getattr(channel, "type", None)
        return t in {discord.ChannelType.voice, discord.ChannelType.stage_voice}

    def _time_of_day_greeting(self) -> str:
        hour = time.localtime().tm_hour
        if 5 <= hour < 12:
            return "Good morning"
        if 12 <= hour < 18:
            return "Good afternoon"
        return "Good evening"

    def _random_greeting(self, name: str) -> str:
        return random.choice(
            [
                f"Hello {name}",
                f"Hey {name}",
                f"Good to see you {name}",
                f"{name} has joined the chat",
            ]
        )

    def _random_farewell(self) -> str:
        return random.choice(["See ya", "Bye", "Until next time"])

    def _today_key(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime())

    async def get_user_greeting_name(self, member: discord.Member) -> str:
        nickname = await self.get_user_nickname(member.id)
        if nickname:
            return nickname
        return member.display_name

    # -------------------- Default Behaviour --------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Default behaviour: if someone types in a voice channel's chat, join that VC and speak.
        if message.author.bot:
            return
        if not message.guild:
            return
        if not message.content or not message.content.strip():
            return

        settings = await self.get_settings(message.guild.id)
        if not settings.get("auto_read_messages", True):
            return

        channel = message.channel
        if not isinstance(channel, discord.abc.GuildChannel):
            return
        if not self._is_voice_chat_text_channel(channel):
            return

        if not isinstance(message.author, discord.Member):
            return

        member = message.author
        if not member.voice or not member.voice.channel:
            return

        if member.voice.channel.id != channel.id:
            return

        ok = await self.ensure_connected(message.guild, member.voice.channel)
        if not ok:
            return

        await self._upsert_user_display_name(member)

        voice_id = await self.get_user_voice(member.id)
        voice_id = self._effective_voice_id(settings, voice_id, allow_default=False)

        state = self.get_state(message.guild.id)
        text = message.content
        is_status = False

        def _is_image_attachment(att: discord.Attachment) -> bool:
            ct = (att.content_type or "").lower()
            if ct.startswith("image/"):
                return True
            name = (att.filename or "").lower()
            return name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"))

        def _is_video_attachment(att: discord.Attachment) -> bool:
            ct = (att.content_type or "").lower()
            if ct.startswith("video/"):
                return True
            name = (att.filename or "").lower()
            return name.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi", ".wmv", ".flv", ".m4v"))

        has_image = any(_is_image_attachment(a) for a in message.attachments)
        has_video = any(_is_video_attachment(a) for a in message.attachments)
        if not (has_image or has_video):
            for emb in message.embeds:
                et = (getattr(emb, "type", "") or "").lower()
                if et == "image":
                    has_image = True
                elif et == "video":
                    has_video = True

        if has_image:
            speak_name = await self.get_user_speak_name(member)
            text = f"{speak_name} posted an image"
            is_status = True
        elif has_video:
            speak_name = await self.get_user_speak_name(member)
            text = f"{speak_name} posted a video"
            is_status = True
        else:
            lowered = (message.content or "").lower()
            if "http://" in lowered or "https://" in lowered:
                speak_name = await self.get_user_speak_name(member)
                text = f"{speak_name} posted a link"
                is_status = True

        if not is_status and state.last_speaker_id != member.id:
            speak_name = await self.get_user_speak_name(member)
            text = f'{speak_name} said. "{text}"'
        state.last_speaker_id = member.id
        await state.queue.put(QueueItem(text=text, voice_id=voice_id))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if not member.guild:
            return

        state = self.get_state(member.guild.id)

        # If the bot is disconnected, clear state + unlock.
        if self.bot.user and member.id == self.bot.user.id:
            if before.channel and after.channel is None:
                await self.disconnect(member.guild.id, "disconnected")
            return

        bot_channel = None
        if state.voice_client and state.voice_client.is_connected() and state.voice_client.channel:
            bot_channel = state.voice_client.channel

        # Auto-join: follow users who opted in, as long as we're not already with people.
        if not member.bot and after.channel is not None:
            joined_channel = before.channel is None or before.channel.id != after.channel.id
            if joined_channel:
                wants_auto = await self.get_user_auto_join(member.id)
                if wants_auto:
                    target_channel = after.channel
                    if bot_channel and bot_channel.id != target_channel.id:
                        non_bot_members = [m for m in bot_channel.members if not m.bot]
                        if len(non_bot_members) > 0:
                            # Don't leave people already with the bot.
                            pass
                        else:
                            await self.disconnect(member.guild.id, "auto_join_move")
                            await self.ensure_connected(member.guild, target_channel)
                    elif bot_channel is None:
                        await self.ensure_connected(member.guild, target_channel)

        # Greetings/farewells only make sense when the bot is already in a voice channel.
        if bot_channel is not None and not member.bot:
            joined_bot_channel = (
                after.channel is not None
                and after.channel.id == bot_channel.id
                and (before.channel is None or before.channel.id != bot_channel.id)
            )
            left_bot_channel = (
                before.channel is not None
                and before.channel.id == bot_channel.id
                and (after.channel is None or after.channel.id != bot_channel.id)
            )

            if joined_bot_channel or left_bot_channel:
                settings = await self.get_settings(member.guild.id)
                default_voice = str(settings.get("default_voice_id", FALLBACK_VOICE))
                voice_id = self._effective_voice_id(settings, default_voice, allow_default=True)
                name = await self.get_user_greeting_name(member)
                today_key = self._today_key()

                if joined_bot_channel and settings.get("greet_on_join"):
                    greeting_text = f"{self._time_of_day_greeting()}, {name}"
                    db = getattr(self.bot, "db", None)
                    last_seen = None
                    if db is not None:
                        try:
                            last_seen = await db.get_member_last_seen(member.guild.id, member.id)
                        except Exception as exc:
                            logger.warning("Failed to read member_seen: guild=%s user=%s err=%s", member.guild.id, member.id, exc)

                    if last_seen == today_key:
                        greeting_text = f"Welcome back {name}"
                    else:
                        greeting_text = self._random_greeting(name)

                    await asyncio.sleep(2)
                    if not (state.voice_client and state.voice_client.is_connected()):
                        return
                    await self.ensure_worker(member.guild.id)
                    await state.queue.put(QueueItem(text=greeting_text, voice_id=voice_id, volume=0.8))

                    if db is not None:
                        try:
                            await db.upsert_member_last_seen(member.guild.id, member.id, today_key, int(time.time()))
                        except Exception as exc:
                            logger.warning(
                                "Failed to upsert member_seen: guild=%s user=%s err=%s",
                                member.guild.id,
                                member.id,
                                exc,
                            )

                if left_bot_channel and settings.get("farewell_on_leave"):
                    await self.ensure_worker(member.guild.id)
                    await state.queue.put(
                        QueueItem(text=f"{self._random_farewell()} {name}", voice_id=voice_id, volume=0.8)
                    )

        if state.voice_client and state.voice_client.is_connected():
            await self.check_should_leave(member.guild)

    # -------------------- Slash Commands --------------------

    @app_commands.command(name="leave", description="Disconnect the bot from voice in this server")
    async def slash_leave(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        await self.disconnect(interaction.guild.id, "slash_leave")
        await interaction.response.send_message("Disconnected.", ephemeral=True)

    @app_commands.command(name="tts", description="Speak text in your current voice channel")
    @app_commands.describe(text="Text to speak")
    async def slash_tts(self, interaction: discord.Interaction, text: str) -> None:
        if not interaction.guild:
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
            return

        ok = await self.ensure_connected(interaction.guild, member.voice.channel)
        if not ok:
            state = self.get_state(interaction.guild.id)
            locked_id = state.voice_channel_id
            msg = "I'm currently locked to another voice channel."
            if locked_id:
                msg = f"I'm currently locked to <#{locked_id}>. Try again once it's empty (or use /leave)."
            await interaction.response.send_message(msg, ephemeral=True)
            return

        await self._upsert_user_display_name(member)
        settings = await self.get_settings(interaction.guild.id)
        voice_id = await self.get_user_voice(member.id)
        voice_id = self._effective_voice_id(settings, voice_id, allow_default=False)

        state = self.get_state(interaction.guild.id)
        await state.queue.put(QueueItem(text=text, voice_id=voice_id))
        await interaction.response.send_message("Queued.", ephemeral=True)

    @app_commands.command(name="voice", description="View or set your personal TTS voice")
    @app_commands.describe(voice_id="Voice ID (autocomplete). Use 'reset' to clear")
    async def slash_voice(self, interaction: discord.Interaction, voice_id: Optional[str] = None) -> None:
        if not interaction.guild:
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        member = interaction.user
        await self._upsert_user_display_name(member)

        settings = await self.get_settings(interaction.guild.id)
        default_voice = str(settings.get("default_voice_id", FALLBACK_VOICE))
        allowed = self._allowed_voice_ids(settings)

        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return

        if voice_id is None:
            saved_voice = await self.get_user_voice(member.id)
            saved_voice = saved_voice or default_voice
            effective_voice = self._effective_voice_id(settings, saved_voice, allow_default=False)

            friendly = VOICE_ID_TO_NAME.get(effective_voice)
            suffix = f" ({friendly})" if friendly else ""

            note = ""
            if allowed is not None and saved_voice != effective_voice:
                note = (
                    f"\nNote: Your saved voice (`{saved_voice}`) isn't allowed in this server, so I'll use "
                    f"`{effective_voice}` instead."
                )

            await interaction.response.send_message(
                f"Your voice is `{effective_voice}`{suffix}.{note}\nTip: use `/set voice` to pick from a menu.",
                ephemeral=True,
            )
            return

        voice_id = voice_id.strip()
        if not voice_id:
            await interaction.response.send_message("Pick a voice, or run `/voice` to view your current one.", ephemeral=True)
            return

        if voice_id.lower() in {"reset", "default"}:
            replacement = self._user_default_voice(settings)
            await db.set_user_voice(member.id, member.display_name, replacement, int(time.time()))
            self.user_voice_cache[member.id] = replacement
            await interaction.response.send_message(
                f"Reset your voice to `{replacement}` (bot voice is reserved).", ephemeral=True
            )
            return

        if voice_id == default_voice:
            await interaction.response.send_message(
                "That voice is reserved for the bot. Please choose a different voice.",
                ephemeral=True,
            )
            return

        if allowed is not None and voice_id not in allowed:
            await interaction.response.send_message(
                f"`{voice_id}` isn't allowed in this server. Ask an admin to allow it in the Web UI settings.",
                ephemeral=True,
            )
            return

        await db.set_user_voice(member.id, member.display_name, voice_id, int(time.time()))
        self.user_voice_cache[member.id] = voice_id

        friendly = VOICE_ID_TO_NAME.get(voice_id)
        suffix = f" ({friendly})" if friendly else ""
        await interaction.response.send_message(f"Set your voice to `{voice_id}`{suffix}.", ephemeral=True)

    def _voice_autocomplete(
        self,
        current: str,
        *,
        allowed_voice_ids: Optional[set[str]] = None,
        exclude_voice_ids: Optional[set[str]] = None,
    ) -> list[app_commands.Choice[str]]:
        current = (current or "").strip().lower()
        excluded = exclude_voice_ids or set()

        def mk_choice(voice_id: str) -> app_commands.Choice[str]:
            name = VOICE_ID_TO_NAME.get(voice_id, voice_id)
            label = f"{name} ({voice_id})" if name != voice_id else voice_id
            return app_commands.Choice(name=label[:100], value=voice_id)

        choices: list[app_commands.Choice[str]] = [app_commands.Choice(name="reset (clear preference)", value="reset")]

        def is_allowed(voice_id: str) -> bool:
            if voice_id in excluded:
                return False
            return allowed_voice_ids is None or voice_id in allowed_voice_ids

        if not current:
            seen: set[str] = set()
            for vid in POPULAR_VOICE_IDS:
                if len(choices) >= 25:
                    break
                if not is_allowed(vid):
                    continue
                choices.append(mk_choice(vid))
                seen.add(vid)

            if allowed_voice_ids is None:
                return choices

            for vid, _name in ALL_VOICES:
                if len(choices) >= 25:
                    break
                if vid in seen or not is_allowed(vid):
                    continue
                choices.append(mk_choice(vid))
                seen.add(vid)

            if len(choices) < 25:
                for vid in sorted(allowed_voice_ids):
                    if len(choices) >= 25:
                        break
                    if vid in seen:
                        continue
                    choices.append(mk_choice(vid))
                    seen.add(vid)

            return choices

        for vid, name in VOICE_ID_TO_NAME.items():
            if not is_allowed(vid):
                continue
            hay = f"{vid} {name}".lower()
            if current in hay:
                choices.append(mk_choice(vid))
                if len(choices) >= 25:
                    break

        if allowed_voice_ids is not None and len(choices) < 25:
            for vid in sorted(allowed_voice_ids):
                if len(choices) >= 25:
                    break
                if vid in VOICE_ID_TO_NAME:
                    continue
                if current in vid.lower():
                    choices.append(mk_choice(vid))

        return choices

    @slash_voice.autocomplete("voice_id")
    async def voice_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        allowed: Optional[set[str]] = None
        if interaction.guild:
            settings = await self.get_settings(interaction.guild.id)
            allowed = self._allowed_voice_ids(settings)
            default_voice = str(settings.get("default_voice_id", FALLBACK_VOICE))
            return self._voice_autocomplete(current, allowed_voice_ids=allowed, exclude_voice_ids={default_voice})
        return self._voice_autocomplete(current, allowed_voice_ids=allowed)

    # -------------------- /set voice (Menu) --------------------

    @set_group.command(name="voice", description="Pick your voice from a menu (or search)")
    @app_commands.describe(voice_id="Leave empty to open the picker. Or type to search.")
    async def set_voice(self, interaction: discord.Interaction, voice_id: Optional[str] = None) -> None:
        if not interaction.guild:
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        member = interaction.user
        await self._upsert_user_display_name(member)

        settings = await self.get_settings(interaction.guild.id)
        default_voice = str(settings.get("default_voice_id", FALLBACK_VOICE))
        reserved_voice = default_voice
        allowed = self._allowed_voice_ids(settings)
        saved_voice = await self.get_user_voice(member.id) or default_voice
        current_voice = self._effective_voice_id(settings, saved_voice, allow_default=False)

        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return

        if voice_id is None:
            friendly = VOICE_ID_TO_NAME.get(current_voice)
            suffix = f" ({friendly})" if friendly else ""
            note = ""
            if allowed is not None and saved_voice != current_voice:
                note = (
                    f"\nNote: Your saved voice (`{saved_voice}`) isn't allowed in this server, so I'll use "
                    f"`{current_voice}` instead."
                )
            view = VoicePickerView(
                self,
                member,
                voices=self._voice_items_for_settings(settings, exclude_voice_ids={reserved_voice}),
                default_voice=self._user_default_voice(settings),
                current_voice=current_voice,
                allowed_voice_ids=allowed,
                page=0,
            )
            await interaction.response.send_message(
                f"Current voice: `{current_voice}`{suffix}.{note}\nSelect a new voice:",
                ephemeral=True,
                view=view,
            )
            return

        # Allow setting via typed + autocomplete too.
        voice_id = voice_id.strip()
        if not voice_id:
            await interaction.response.send_message("Pick a voice, or run `/set voice` to open the picker.", ephemeral=True)
            return

        if voice_id.lower() in {"reset", "default"}:
            replacement = self._user_default_voice(settings)
            await self._set_voice_pref(member, replacement)
            await interaction.response.send_message(
                f"Reset your voice to `{replacement}` (bot voice is reserved).",
                ephemeral=True,
            )
            return

        if voice_id == default_voice:
            await interaction.response.send_message(
                "That voice is reserved for the bot. Please choose a different voice.",
                ephemeral=True,
            )
            return

        if allowed is not None and voice_id not in allowed:
            await interaction.response.send_message(
                f"`{voice_id}` isn't allowed in this server. Ask an admin to allow it in the Web UI settings.",
                ephemeral=True,
            )
            return

        await self._set_voice_pref(member, voice_id)
        friendly = VOICE_ID_TO_NAME.get(voice_id)
        suffix = f" ({friendly})" if friendly else ""
        await interaction.response.send_message(f"Set your voice to `{voice_id}`{suffix}.", ephemeral=True)

    @set_voice.autocomplete("voice_id")
    async def set_voice_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        allowed: Optional[set[str]] = None
        if interaction.guild:
            settings = await self.get_settings(interaction.guild.id)
            allowed = self._allowed_voice_ids(settings)
            default_voice = str(settings.get("default_voice_id", FALLBACK_VOICE))
            return self._voice_autocomplete(current, allowed_voice_ids=allowed, exclude_voice_ids={default_voice})
        return self._voice_autocomplete(current, allowed_voice_ids=allowed)

    # -------------------- /set nickname --------------------

    @set_group.command(name="nickname", description="Set the name the bot will speak for you")
    @app_commands.describe(nickname="Leave empty to view. Use 'reset' to clear.")
    async def set_nickname(self, interaction: discord.Interaction, nickname: Optional[str] = None) -> None:
        if not interaction.guild:
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        member = interaction.user
        await self._upsert_user_display_name(member)

        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.followup.send("Database is not configured.", ephemeral=True)
            return

        if nickname is None:
            current = await self.get_user_nickname(member.id)
            if current:
                await interaction.followup.send(
                    f"Your nickname is set to `{current}` (this is what I'll speak).",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"You don't have a nickname set. I'll use your Discord display name (`{member.display_name}`).\n"
                    "Set one with `/set nickname <name>`.",
                    ephemeral=True,
                )
            return

        nickname = " ".join((nickname or "").strip().split())
        if not nickname or nickname.lower() in {"reset", "clear", "default"}:
            await self._reset_nickname_pref(member)
            await interaction.followup.send(
                f"Cleared your nickname. I'll use your Discord display name (`{member.display_name}`).",
                ephemeral=True,
            )
            return

        if len(nickname) > 64:
            await interaction.followup.send("Nickname must be 64 characters or fewer.", ephemeral=True)
            return

        await self._set_nickname_pref(member, nickname)
        await interaction.followup.send(f"Saved! Your nickname is now `{nickname}`.", ephemeral=True)

    # -------------------- /set followme --------------------

    @set_group.command(name="followme", description="Have the bot auto-join your voice channel")
    @app_commands.describe(enabled="Enable or disable auto-join")
    async def set_followme(self, interaction: discord.Interaction, enabled: Optional[bool] = None) -> None:
        if not interaction.guild:
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        member = interaction.user
        await self._upsert_user_display_name(member)

        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.followup.send("Database is not configured.", ephemeral=True)
            return

        if enabled is None:
            current = await self.get_user_auto_join(member.id)
            status = "enabled" if current else "disabled"
            await interaction.followup.send(f"Auto-join is currently `{status}` for you.", ephemeral=True)
            return

        await self._set_auto_join_pref(member, enabled)
        status = "enabled" if enabled else "disabled"
        await interaction.followup.send(f"Auto-join is now `{status}` for you.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TTSCog(bot))
