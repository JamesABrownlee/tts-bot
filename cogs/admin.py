import contextlib
import math
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.config import ALL_VOICES, FALLBACK_VOICE, VOICE_ID_TO_NAME
from utils.logger import get_logger

logger = get_logger("admin")

def _settings_summary(settings: dict[str, Any]) -> str:
    default_voice = str(settings.get("default_voice_id", FALLBACK_VOICE))
    fallback_voice = str(settings.get("fallback_voice", FALLBACK_VOICE))

    allowed = settings.get("allowed_voice_ids") or []
    allowed_count = len(allowed) if isinstance(allowed, list) else 0

    return "\n".join(
        [
            f"- max_tts_chars: `{settings.get('max_tts_chars')}`",
            f"- default_voice_id: `{default_voice}` ({VOICE_ID_TO_NAME.get(default_voice, default_voice)})",
            f"- fallback_voice: `{fallback_voice}` ({VOICE_ID_TO_NAME.get(fallback_voice, fallback_voice)})",
            f"- auto_read_messages: `{'enabled' if settings.get('auto_read_messages') else 'disabled'}`",
            f"- leave_when_alone: `{'enabled' if settings.get('leave_when_alone') else 'disabled'}`",
            f"- greet_on_join: `{'enabled' if settings.get('greet_on_join') else 'disabled'}`",
            f"- farewell_on_leave: `{'enabled' if settings.get('farewell_on_leave') else 'disabled'}`",
            f"- restrict_voices: `{'enabled' if settings.get('restrict_voices') else 'disabled'}`",
            f"- allowed_voice_ids: `{allowed_count}` voices",
        ]
    )


class _AdminBaseView(discord.ui.View):
    def __init__(self, bot: commands.Bot, *, guild_id: int, invoker_id: int, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.bot = bot
        self.guild_id = int(guild_id)
        self.invoker_id = int(invoker_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("This control panel is for a different server.", ephemeral=True)
            return False

        if not interaction.user or interaction.user.id != self.invoker_id:
            await interaction.response.send_message("This control panel isn't for you.", ephemeral=True)
            return False

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        perms_ok = bool(member and (member.guild_permissions.manage_guild or member.guild_permissions.administrator))
        if not perms_ok:
            await interaction.response.send_message("You need **Manage Server** to use this.", ephemeral=True)
            return False

        return True

    def _store(self):
        store = getattr(self.bot, "guild_settings", None)
        if store is None:
            raise RuntimeError("Guild settings store is not configured")
        return store


class MaxCharsModal(discord.ui.Modal, title="Set Max TTS Characters"):
    value = discord.ui.TextInput(
        label="Max TTS Characters",
        placeholder="300",
        required=True,
        max_length=4,
    )

    def __init__(self, *, panel_message: Optional[discord.Message], bot: commands.Bot, guild_id: int, invoker_id: int) -> None:
        super().__init__(timeout=300)
        self._panel_message = panel_message
        self._bot = bot
        self._guild_id = int(guild_id)
        self._invoker_id = int(invoker_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        perms_ok = bool(member and (member.guild_permissions.manage_guild or member.guild_permissions.administrator))
        if not perms_ok or not interaction.user or interaction.user.id != self._invoker_id:
            await interaction.response.send_message("You need **Manage Server** to use this.", ephemeral=True)
            return

        try:
            max_chars = int(str(self.value.value).strip())
        except ValueError:
            await interaction.response.send_message("Max characters must be an integer.", ephemeral=True)
            return

        store = getattr(self._bot, "guild_settings", None)
        if store is None:
            await interaction.response.send_message("Settings store not configured.", ephemeral=True)
            return

        try:
            await store.update(self._guild_id, {"max_tts_chars": max_chars})
        except Exception as exc:
            await interaction.response.send_message(f"Error: {exc}", ephemeral=True)
            return

        with contextlib.suppress(Exception):
            if self._panel_message:
                settings = await store.get(self._guild_id)
                view = SettingsPanelView(self._bot, guild_id=self._guild_id, invoker_id=self._invoker_id, settings=settings)
                await self._panel_message.edit(content=view.render_content(), view=view)

        await interaction.response.send_message("Saved.", ephemeral=True)


class SettingsPanelView(_AdminBaseView):
    def __init__(self, bot: commands.Bot, *, guild_id: int, invoker_id: int, settings: dict[str, Any]) -> None:
        super().__init__(bot, guild_id=guild_id, invoker_id=invoker_id, timeout=300)
        self.settings = dict(settings)
        self.message: Optional[discord.Message] = None

        self._btn_auto = discord.ui.Button(label="Auto Read", style=discord.ButtonStyle.secondary, row=0)
        self._btn_leave = discord.ui.Button(label="Leave When Alone", style=discord.ButtonStyle.secondary, row=0)
        self._btn_greet = discord.ui.Button(label="Greet On Join", style=discord.ButtonStyle.secondary, row=0)
        self._btn_farewell = discord.ui.Button(label="Farewell On Leave", style=discord.ButtonStyle.secondary, row=0)
        self._btn_restrict = discord.ui.Button(label="Restrict Voices", style=discord.ButtonStyle.secondary, row=1)

        self._btn_default_voice = discord.ui.Button(label="Set Default Voice", style=discord.ButtonStyle.primary, row=1)
        self._btn_fallback_voice = discord.ui.Button(label="Set Fallback Voice", style=discord.ButtonStyle.primary, row=1)
        self._btn_allowed = discord.ui.Button(label="Allowed Voices", style=discord.ButtonStyle.primary, row=2)
        self._btn_max_chars = discord.ui.Button(label="Set Max Chars", style=discord.ButtonStyle.primary, row=2)
        self._btn_close = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, row=2)

        self._btn_auto.callback = self._on_toggle_auto
        self._btn_leave.callback = self._on_toggle_leave
        self._btn_greet.callback = self._on_toggle_greet
        self._btn_farewell.callback = self._on_toggle_farewell
        self._btn_restrict.callback = self._on_toggle_restrict

        self._btn_default_voice.callback = self._on_set_default_voice
        self._btn_fallback_voice.callback = self._on_set_fallback_voice
        self._btn_allowed.callback = self._on_allowed_voices
        self._btn_max_chars.callback = self._on_set_max_chars
        self._btn_close.callback = self._on_close

        self.add_item(self._btn_auto)
        self.add_item(self._btn_leave)
        self.add_item(self._btn_greet)
        self.add_item(self._btn_farewell)
        self.add_item(self._btn_restrict)
        self.add_item(self._btn_default_voice)
        self.add_item(self._btn_fallback_voice)
        self.add_item(self._btn_allowed)
        self.add_item(self._btn_max_chars)
        self.add_item(self._btn_close)

        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        def style_for(flag: bool) -> discord.ButtonStyle:
            return discord.ButtonStyle.success if flag else discord.ButtonStyle.secondary

        self._btn_auto.style = style_for(bool(self.settings.get("auto_read_messages")))
        self._btn_leave.style = style_for(bool(self.settings.get("leave_when_alone")))
        self._btn_greet.style = style_for(bool(self.settings.get("greet_on_join")))
        self._btn_farewell.style = style_for(bool(self.settings.get("farewell_on_leave")))
        self._btn_restrict.style = style_for(bool(self.settings.get("restrict_voices")))

    def render_content(self, *, error: Optional[str] = None) -> str:
        guild = self.bot.get_guild(self.guild_id)
        guild_name = guild.name if guild else str(self.guild_id)

        header = f"**Admin Settings — {guild_name}**\n"
        hint = "Tip: You can also configure these in the Web UI `/settings` page.\n"
        body = _settings_summary(self.settings)
        err = f"\n\n**Error:** {error}" if error else ""
        return header + hint + body + err

    async def _apply_patch(self, interaction: discord.Interaction, patch: dict[str, Any]) -> None:
        store = self._store()
        try:
            self.settings = await store.update(self.guild_id, patch)
        except Exception as exc:
            logger.warning("Admin settings update failed: guild=%s patch=%s err=%s", self.guild_id, patch, exc)
            await interaction.edit_original_response(content=self.render_content(error=str(exc)), view=self)
            return

        self._refresh_buttons()
        await interaction.edit_original_response(content=self.render_content(), view=self)

    async def _toggle_bool(self, interaction: discord.Interaction, key: str) -> None:
        await interaction.response.defer()
        current = bool(self.settings.get(key))
        await self._apply_patch(interaction, {key: not current})

    async def _on_toggle_auto(self, interaction: discord.Interaction) -> None:
        await self._toggle_bool(interaction, "auto_read_messages")

    async def _on_toggle_leave(self, interaction: discord.Interaction) -> None:
        await self._toggle_bool(interaction, "leave_when_alone")

    async def _on_toggle_greet(self, interaction: discord.Interaction) -> None:
        await self._toggle_bool(interaction, "greet_on_join")

    async def _on_toggle_farewell(self, interaction: discord.Interaction) -> None:
        await self._toggle_bool(interaction, "farewell_on_leave")

    async def _on_toggle_restrict(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        enabled = bool(self.settings.get("restrict_voices"))
        patch: dict[str, Any] = {"restrict_voices": not enabled}
        if not enabled:
            allowed = set(self.settings.get("allowed_voice_ids") or [])
            allowed.add(str(self.settings.get("fallback_voice", FALLBACK_VOICE)))
            allowed.add(str(self.settings.get("default_voice_id", FALLBACK_VOICE)))
            patch["allowed_voice_ids"] = list(allowed)
        await self._apply_patch(interaction, patch)

    async def _on_set_default_voice(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        view = VoiceSelectView(
            self.bot,
            guild_id=self.guild_id,
            invoker_id=self.invoker_id,
            settings=self.settings,
            field="default_voice_id",
            title="Set Server Default Voice",
        )
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)

    async def _on_set_fallback_voice(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        view = VoiceSelectView(
            self.bot,
            guild_id=self.guild_id,
            invoker_id=self.invoker_id,
            settings=self.settings,
            field="fallback_voice",
            title="Set Server Fallback Voice",
        )
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)

    async def _on_allowed_voices(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        view = AllowedVoicesMenuView(
            self.bot, guild_id=self.guild_id, invoker_id=self.invoker_id, settings=self.settings
        )
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)

    async def _on_set_max_chars(self, interaction: discord.Interaction) -> None:
        modal = MaxCharsModal(
            panel_message=interaction.message,
            bot=self.bot,
            guild_id=self.guild_id,
            invoker_id=self.invoker_id,
        )
        await interaction.response.send_modal(modal)

    async def _on_close(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            child.disabled = True
        self.stop()
        await interaction.response.edit_message(view=self)


class VoiceSelectView(_AdminBaseView):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        guild_id: int,
        invoker_id: int,
        settings: dict[str, Any],
        field: str,
        title: str,
        page: int = 0,
    ) -> None:
        super().__init__(bot, guild_id=guild_id, invoker_id=invoker_id, timeout=300)
        self.settings = dict(settings)
        self.field = field
        self.title = title
        self.page = page
        self.per_page = 25
        self.message: Optional[discord.Message] = None

        self._render()

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(ALL_VOICES) / self.per_page))

    def _page_items(self) -> list[tuple[str, str]]:
        start = self.page * self.per_page
        end = start + self.per_page
        return ALL_VOICES[start:end]

    def render_content(self, *, error: Optional[str] = None) -> str:
        current_value = str(self.settings.get(self.field, ""))
        err = f"\n\n**Error:** {error}" if error else ""
        return (
            f"**{self.title}**\n"
            f"Current: `{current_value}` ({VOICE_ID_TO_NAME.get(current_value, current_value)})\n"
            f"Page {self.page + 1}/{self.page_count}.{err}"
        )

    def _render(self) -> None:
        self.clear_items()

        options: list[discord.SelectOption] = []
        for voice_id, name in self._page_items():
            label = (name or voice_id)[:100]
            description = voice_id[:100]
            options.append(discord.SelectOption(label=label, value=voice_id, description=description))

        select = discord.ui.Select(
            placeholder="Pick a voice…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        select.callback = self._on_select  # type: ignore[assignment]
        self.add_item(select)

        prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary, disabled=self.page <= 0, row=1)
        next_btn = discord.ui.Button(
            label="Next", style=discord.ButtonStyle.secondary, disabled=self.page >= (self.page_count - 1), row=1
        )
        back_btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.primary, row=1)

        prev_btn.callback = self._on_prev  # type: ignore[assignment]
        next_btn.callback = self._on_next  # type: ignore[assignment]
        back_btn.callback = self._on_back  # type: ignore[assignment]

        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(back_btn)

    async def _save_voice(self, interaction: discord.Interaction, voice_id: str) -> None:
        store = self._store()
        await interaction.response.defer()

        patch: dict[str, Any] = {self.field: voice_id}
        if self.settings.get("restrict_voices"):
            allowed = set(self.settings.get("allowed_voice_ids") or [])
            default_voice = voice_id if self.field == "default_voice_id" else str(self.settings.get("default_voice_id", FALLBACK_VOICE))
            fallback_voice = voice_id if self.field == "fallback_voice" else str(self.settings.get("fallback_voice", FALLBACK_VOICE))
            allowed.add(default_voice)
            allowed.add(fallback_voice)
            patch["allowed_voice_ids"] = list(allowed)

        try:
            settings = await store.update(self.guild_id, patch)
        except Exception as exc:
            self.settings = await store.get(self.guild_id)
            self._render()
            await interaction.edit_original_response(content=self.render_content(error=str(exc)), view=self)
            return

        view = SettingsPanelView(self.bot, guild_id=self.guild_id, invoker_id=self.invoker_id, settings=settings)
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select = next((c for c in self.children if isinstance(c, discord.ui.Select)), None)
        if not select or not select.values:
            await interaction.response.defer()
            return
        await self._save_voice(interaction, select.values[0])

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        self._render()
        await interaction.edit_original_response(content=self.render_content(), view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.page = min(self.page_count - 1, self.page + 1)
        self._render()
        await interaction.edit_original_response(content=self.render_content(), view=self)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        store = self._store()
        settings = await store.get(self.guild_id)
        view = SettingsPanelView(self.bot, guild_id=self.guild_id, invoker_id=self.invoker_id, settings=settings)
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)


class AllowedVoicesMenuView(_AdminBaseView):
    def __init__(self, bot: commands.Bot, *, guild_id: int, invoker_id: int, settings: dict[str, Any]) -> None:
        super().__init__(bot, guild_id=guild_id, invoker_id=invoker_id, timeout=300)
        self.settings = dict(settings)
        self.message: Optional[discord.Message] = None

        self._btn_add = discord.ui.Button(label="Add Voices", style=discord.ButtonStyle.primary, row=0)
        self._btn_remove = discord.ui.Button(label="Remove Voices", style=discord.ButtonStyle.primary, row=0)
        self._btn_allow_all = discord.ui.Button(label="Allow All", style=discord.ButtonStyle.secondary, row=1)
        self._btn_clear = discord.ui.Button(label="Clear", style=discord.ButtonStyle.secondary, row=1)
        self._btn_back = discord.ui.Button(label="Back", style=discord.ButtonStyle.primary, row=1)

        self._btn_add.callback = self._on_add
        self._btn_remove.callback = self._on_remove
        self._btn_allow_all.callback = self._on_allow_all
        self._btn_clear.callback = self._on_clear
        self._btn_back.callback = self._on_back

        self.add_item(self._btn_add)
        self.add_item(self._btn_remove)
        self.add_item(self._btn_allow_all)
        self.add_item(self._btn_clear)
        self.add_item(self._btn_back)

    def _required_voice_ids(self) -> set[str]:
        req = {
            str(self.settings.get("fallback_voice", FALLBACK_VOICE)),
            str(self.settings.get("default_voice_id", FALLBACK_VOICE)),
        }
        return {v for v in req if v}

    def render_content(self, *, error: Optional[str] = None) -> str:
        allowed = self.settings.get("allowed_voice_ids") or []
        allowed_list = allowed if isinstance(allowed, list) else []
        allowed_set = set(str(v) for v in allowed_list if str(v).strip())

        req = self._required_voice_ids()
        restrict = bool(self.settings.get("restrict_voices"))

        sample = []
        for vid in list(allowed_list)[:10]:
            vid = str(vid)
            if not vid:
                continue
            sample.append(f"`{vid}`")
        sample_txt = ", ".join(sample) if sample else "(none)"

        err = f"\n\n**Error:** {error}" if error else ""
        return (
            "**Allowed Voices**\n"
            f"- restrict_voices: `{'enabled' if restrict else 'disabled'}`\n"
            f"- required: {', '.join([f'`{v}`' for v in sorted(req)])}\n"
            f"- allowed: `{len(allowed_set)}` voices\n"
            f"- sample: {sample_txt}\n"
            "\nUse **Add**/**Remove** to edit the allowlist."
            + err
        )

    async def _save_allowed(self, interaction: discord.Interaction, allowed_voice_ids: list[str]) -> None:
        store = self._store()
        await interaction.response.defer()

        patch: dict[str, Any] = {"allowed_voice_ids": allowed_voice_ids}
        if self.settings.get("restrict_voices"):
            req = self._required_voice_ids()
            merged = list(dict.fromkeys([*allowed_voice_ids, *sorted(req)]))
            patch["allowed_voice_ids"] = merged

        try:
            self.settings = await store.update(self.guild_id, patch)
        except Exception as exc:
            await interaction.edit_original_response(content=self.render_content(error=str(exc)), view=self)
            return

        await interaction.edit_original_response(content=self.render_content(), view=self)

    async def _on_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        view = AllowedVoicesPickerView(
            self.bot,
            guild_id=self.guild_id,
            invoker_id=self.invoker_id,
            settings=self.settings,
            mode="add",
        )
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)

    async def _on_remove(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        view = AllowedVoicesPickerView(
            self.bot,
            guild_id=self.guild_id,
            invoker_id=self.invoker_id,
            settings=self.settings,
            mode="remove",
        )
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)

    async def _on_allow_all(self, interaction: discord.Interaction) -> None:
        all_ids = [voice_id for voice_id, _name in ALL_VOICES]
        await self._save_allowed(interaction, all_ids)

    async def _on_clear(self, interaction: discord.Interaction) -> None:
        if self.settings.get("restrict_voices"):
            await self._save_allowed(interaction, sorted(self._required_voice_ids()))
        else:
            await self._save_allowed(interaction, [])

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        store = self._store()
        settings = await store.get(self.guild_id)
        view = SettingsPanelView(self.bot, guild_id=self.guild_id, invoker_id=self.invoker_id, settings=settings)
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)


class AllowedVoicesPickerView(_AdminBaseView):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        guild_id: int,
        invoker_id: int,
        settings: dict[str, Any],
        mode: str,
        page: int = 0,
    ) -> None:
        super().__init__(bot, guild_id=guild_id, invoker_id=invoker_id, timeout=300)
        if mode not in {"add", "remove"}:
            raise ValueError("mode must be add or remove")
        self.settings = dict(settings)
        self.mode = mode
        self.page = page
        self.per_page = 25
        self.message: Optional[discord.Message] = None

        self._render()

    def _required_voice_ids(self) -> set[str]:
        req = {
            str(self.settings.get("fallback_voice", FALLBACK_VOICE)),
            str(self.settings.get("default_voice_id", FALLBACK_VOICE)),
        }
        return {v for v in req if v}

    def _items(self) -> list[tuple[str, str]]:
        if self.mode == "add":
            return ALL_VOICES

        allowed = self.settings.get("allowed_voice_ids") or []
        allowed_list = allowed if isinstance(allowed, list) else []
        allowed_set = {str(v).strip() for v in allowed_list if str(v).strip()}

        if self.settings.get("restrict_voices"):
            allowed_set -= self._required_voice_ids()

        items: list[tuple[str, str]] = []
        for vid in sorted(allowed_set):
            items.append((vid, VOICE_ID_TO_NAME.get(vid, vid)))
        return items

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(self._items()) / self.per_page))

    def _page_items(self) -> list[tuple[str, str]]:
        items = self._items()
        start = self.page * self.per_page
        end = start + self.per_page
        return items[start:end]

    def render_content(self, *, error: Optional[str] = None) -> str:
        allowed = self.settings.get("allowed_voice_ids") or []
        allowed_list = allowed if isinstance(allowed, list) else []
        allowed_count = len({str(v).strip() for v in allowed_list if str(v).strip()})

        action = "Add" if self.mode == "add" else "Remove"
        err = f"\n\n**Error:** {error}" if error else ""
        return f"**{action} Voices**\nAllowed voices: `{allowed_count}`.\nPage {self.page + 1}/{self.page_count}.{err}"

    def _render(self) -> None:
        self.clear_items()

        options: list[discord.SelectOption] = []
        for voice_id, name in self._page_items():
            label = (name or voice_id)[:100]
            description = voice_id[:100]
            options.append(discord.SelectOption(label=label, value=voice_id, description=description))

        if options:
            select = discord.ui.Select(
                placeholder="Select voices…",
                min_values=1,
                max_values=min(25, len(options)),
                options=options,
                row=0,
            )
            select.callback = self._on_select  # type: ignore[assignment]
            self.add_item(select)

        prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary, disabled=self.page <= 0, row=1)
        next_btn = discord.ui.Button(
            label="Next", style=discord.ButtonStyle.secondary, disabled=self.page >= (self.page_count - 1), row=1
        )
        back_btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.primary, row=1)

        prev_btn.callback = self._on_prev  # type: ignore[assignment]
        next_btn.callback = self._on_next  # type: ignore[assignment]
        back_btn.callback = self._on_back  # type: ignore[assignment]

        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(back_btn)

    async def _update_allowed(self, interaction: discord.Interaction, selected_ids: list[str]) -> None:
        store = self._store()
        await interaction.response.defer()

        current_allowed = self.settings.get("allowed_voice_ids") or []
        allowed_list = current_allowed if isinstance(current_allowed, list) else []
        allowed_set = {str(v).strip() for v in allowed_list if str(v).strip()}

        selected = {str(v).strip() for v in selected_ids if str(v).strip()}

        if self.mode == "add":
            allowed_set |= selected
        else:
            allowed_set -= selected

        if self.settings.get("restrict_voices"):
            allowed_set |= self._required_voice_ids()

        patch = {"allowed_voice_ids": list(allowed_set)}
        try:
            settings = await store.update(self.guild_id, patch)
        except Exception as exc:
            self.settings = await store.get(self.guild_id)
            self._render()
            await interaction.edit_original_response(content=self.render_content(error=str(exc)), view=self)
            return

        view = AllowedVoicesMenuView(self.bot, guild_id=self.guild_id, invoker_id=self.invoker_id, settings=settings)
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select = next((c for c in self.children if isinstance(c, discord.ui.Select)), None)
        if not select or not select.values:
            await interaction.response.defer()
            return
        await self._update_allowed(interaction, list(select.values))

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        self._render()
        await interaction.edit_original_response(content=self.render_content(), view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.page = min(self.page_count - 1, self.page + 1)
        self._render()
        await interaction.edit_original_response(content=self.render_content(), view=self)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        store = self._store()
        settings = await store.get(self.guild_id)
        view = AllowedVoicesMenuView(self.bot, guild_id=self.guild_id, invoker_id=self.invoker_id, settings=settings)
        view.message = interaction.message
        await interaction.edit_original_response(content=view.render_content(), view=view)


class AdminCog(commands.Cog):
    admin_group = app_commands.Group(name="admin", description="Admin tools (Manage Server)")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @admin_group.command(name="panel", description="Open an interactive settings panel for this server")
    @app_commands.default_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message("You need **Manage Server** to use this command.", ephemeral=True)
            return

        guild = interaction.guild

        store = getattr(self.bot, "guild_settings", None)
        if store is None:
            await interaction.response.send_message("Settings store not configured.", ephemeral=True)
            return

        settings = await store.get(guild.id)
        view = SettingsPanelView(self.bot, guild_id=guild.id, invoker_id=interaction.user.id, settings=settings)
        await interaction.response.send_message(view.render_content(), ephemeral=True, view=view)
        with contextlib.suppress(Exception):
            view.message = await interaction.original_response()

    @admin_group.command(name="show", description="Show the current settings for this server")
    @app_commands.default_permissions(manage_guild=True)
    async def show(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message("You need **Manage Server** to use this command.", ephemeral=True)
            return

        guild = interaction.guild

        store = getattr(self.bot, "guild_settings", None)
        if store is None:
            await interaction.response.send_message("Settings store not configured.", ephemeral=True)
            return

        settings = await store.get(guild.id)
        await interaction.response.send_message(
            f"**Server Settings — {guild.name}**\n{_settings_summary(settings)}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
