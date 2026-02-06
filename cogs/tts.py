import asyncio
import math
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


@dataclass
class GuildState:
    voice_client: Optional[discord.VoiceClient] = None
    voice_channel_id: Optional[int] = None  # locked voice channel
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    worker: Optional[asyncio.Task] = None
    connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_speaker_id: Optional[int] = None


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
        await self.cog._reset_voice_pref(self.member)
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

    async def get_settings(self, guild_id: Optional[int] = None) -> dict:
        store = getattr(self.bot, "guild_settings", None)
        if store is not None and guild_id is not None:
            try:
                return await store.get(guild_id)
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

    def _effective_voice_id(self, settings: dict, requested_voice_id: Optional[str]) -> str:
        default_voice = str(settings.get("default_voice_id") or FALLBACK_VOICE).strip() or FALLBACK_VOICE
        fallback_voice = str(settings.get("fallback_voice") or FALLBACK_VOICE).strip() or FALLBACK_VOICE
        voice_id = str(requested_voice_id or "").strip() or default_voice

        allowed = self._allowed_voice_ids(settings)
        if allowed is None:
            return voice_id
        if voice_id in allowed:
            return voice_id
        if default_voice in allowed:
            return default_voice
        if fallback_voice in allowed:
            return fallback_voice
        return voice_id

    def _voice_items_for_settings(self, settings: dict) -> list[tuple[str, str]]:
        allowed = self._allowed_voice_ids(settings)
        if allowed is None:
            return ALL_VOICES
        allowed_list = settings.get("allowed_voice_ids") or []
        if not isinstance(allowed_list, list):
            allowed_list = list(allowed)

        items: list[tuple[str, str]] = [(vid, name) for vid, name in ALL_VOICES if vid in allowed]
        known_ids = {vid for vid, _name in items}
        for vid in allowed_list:
            vid = str(vid or "").strip()
            if not vid or vid in known_ids:
                continue
            items.append((vid, VOICE_ID_TO_NAME.get(vid, vid)))
            known_ids.add(vid)

        return items or ALL_VOICES

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
                    await self.play_tts(guild_id, state.voice_client, item.text, item.voice_id)
                finally:
                    state.queue.task_done()

        state.worker = asyncio.create_task(worker_loop())

    async def stop_worker(self, state: GuildState) -> None:
        if state.worker and not state.worker.done():
            await state.queue.put(None)
            await state.worker

    async def play_tts(self, guild_id: int, voice_client: discord.VoiceClient, text: str, voice_id: str) -> None:
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
            # Already connected.
            if state.voice_client and state.voice_client.is_connected() and state.voice_client.channel:
                if state.voice_client.channel.id == voice_channel.id:
                    return True

                # Locked to another channel in the guild.
                return False

            # Connect and lock.
            state.voice_channel_id = voice_channel.id
            state.last_speaker_id = None

            logger.info("Connecting voice: guild=%s channel=%s", guild.id, voice_channel.id)

            try:
                state.voice_client = await voice_channel.connect()
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
        voice_id = self._effective_voice_id(settings, voice_id)

        state = self.get_state(message.guild.id)
        text = message.content
        if state.last_speaker_id != member.id:
            speak_name = await self.get_user_speak_name(member)
            text = f'{speak_name} said "{text}"'
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
                voice_id = self._effective_voice_id(settings, default_voice)
                name = await self.get_user_announcement_name(member)

                if joined_bot_channel and settings.get("greet_on_join"):
                    greeting = self._time_of_day_greeting()
                    await self.ensure_worker(member.guild.id)
                    await state.queue.put(QueueItem(text=f"{greeting}, {name}", voice_id=voice_id))

                if left_bot_channel and settings.get("farewell_on_leave"):
                    await self.ensure_worker(member.guild.id)
                    await state.queue.put(QueueItem(text=f"Goodbye, {name}", voice_id=voice_id))

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
        voice_id = self._effective_voice_id(settings, voice_id)

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
            effective_voice = self._effective_voice_id(settings, saved_voice)

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
            await db.delete_user_voice(member.id, int(time.time()))
            self.user_voice_cache.pop(member.id, None)
            await interaction.response.send_message(f"Reset your voice to default (`{default_voice}`).", ephemeral=True)
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
        self, current: str, *, allowed_voice_ids: Optional[set[str]] = None
    ) -> list[app_commands.Choice[str]]:
        current = (current or "").strip().lower()

        def mk_choice(voice_id: str) -> app_commands.Choice[str]:
            name = VOICE_ID_TO_NAME.get(voice_id, voice_id)
            label = f"{name} ({voice_id})" if name != voice_id else voice_id
            return app_commands.Choice(name=label[:100], value=voice_id)

        choices: list[app_commands.Choice[str]] = [app_commands.Choice(name="reset (clear preference)", value="reset")]

        def is_allowed(voice_id: str) -> bool:
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
        allowed = self._allowed_voice_ids(settings)
        saved_voice = await self.get_user_voice(member.id) or default_voice
        current_voice = self._effective_voice_id(settings, saved_voice)

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
                voices=self._voice_items_for_settings(settings),
                default_voice=default_voice,
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
            await self._reset_voice_pref(member)
            await interaction.response.send_message(f"Reset your voice to default (`{default_voice}`).", ephemeral=True)
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

        member = interaction.user
        await self._upsert_user_display_name(member)

        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.response.send_message("Database is not configured.", ephemeral=True)
            return

        if nickname is None:
            current = await self.get_user_nickname(member.id)
            if current:
                await interaction.response.send_message(
                    f"Your nickname is set to `{current}` (this is what I'll speak).",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"You don't have a nickname set. I'll use your Discord display name (`{member.display_name}`).\n"
                    "Set one with `/set nickname <name>`.",
                    ephemeral=True,
                )
            return

        nickname = " ".join((nickname or "").strip().split())
        if not nickname or nickname.lower() in {"reset", "clear", "default"}:
            await self._reset_nickname_pref(member)
            await interaction.response.send_message(
                f"Cleared your nickname. I'll use your Discord display name (`{member.display_name}`).",
                ephemeral=True,
            )
            return

        if len(nickname) > 64:
            await interaction.response.send_message("Nickname must be 64 characters or fewer.", ephemeral=True)
            return

        await self._set_nickname_pref(member, nickname)
        await interaction.response.send_message(f"Saved! Your nickname is now `{nickname}`.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TTSCog(bot))
