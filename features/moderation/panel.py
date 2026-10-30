"""Control panel section for moderation: channels, automod, exemptions, escalation and anti-raid."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Literal

import discord

from core.panel.editor import Field, Form, SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from features.moderation.cogs.config_cog import Config
from features.moderation.utils.time import humanize, parse_duration

if TYPE_CHECKING:
    from core.bot import Bot

Page = Literal["general", "automod", "exempt", "escalation", "raid"]
PAGES: dict[str, tuple[str, str]] = {
    "general": ("General", "🛡️"),
    "automod": ("Automod", "🤖"),
    "exempt": ("Exemptions", "🎟️"),
    "escalation": ("Escalation", "📈"),
    "raid": ("Anti-raid", "🚨"),
}
FILTERS = [
    ("badwords", "Banned words", "Delete messages with a banned word or phrase."),
    ("invites", "Server invites", "Delete Discord invite links."),
    ("links", "Links", "Delete links to sites that aren't on the allowed list."),
    ("spam", "Spam", "Too many messages in a short time."),
    ("duplicates", "Repeated messages", "The same message again and again."),
    ("mentions", "Mass mentions", "Too many mentions in one message."),
]
MAX_TIMEOUT_SECONDS = 28 * 86400


def _channel(guild: discord.Guild, channel_id: int | None) -> str:
    channel = guild.get_channel(channel_id) if channel_id else None
    return channel.mention if channel else "not set"


def _filter_on(automod: dict, key: str) -> bool:
    value = automod[key]
    return value["enabled"] if isinstance(value, dict) else bool(value)


def _number(text: str, name: str, low: int, high: int) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number.") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


async def status(bot: Bot, guild: discord.Guild) -> str:
    s = await bot.mod_configs.get(guild.id)
    parts = [f"Mod-log {_channel(guild, s.modlog_channel_id)}",
             f"automod {style.ON if s.automod['enabled'] else style.OFF}",
             f"anti-raid {style.ON if s.raid['enabled'] else style.OFF}"]
    if s.raidmode_active:
        parts.insert(0, "🚨 **RAID MODE ON**")
    return " · ".join(parts)


class ModerationEditor(SectionEditor):
    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int, page: Page = "general") -> None:
        super().__init__(bot, guild, author_id)
        self.page: Page = page

    async def build(self) -> None:
        s = await self.bot.mod_configs.get(self.guild.id)
        tabs = self.row(*(
            self.button(label, self._goto(key), emoji=emoji, disabled=key == self.page,
                        style_=discord.ButtonStyle.primary if key == self.page else discord.ButtonStyle.secondary)
            for key, (label, emoji) in PAGES.items()
        ))
        items: list[discord.ui.Item] = [self.text("## 🛡️ Moderation"), tabs, discord.ui.Separator()]
        items += await getattr(self, f"_{self.page}_page")(s)
        self.add_item(self.container(*items))

    # --- pages ------------------------------------------------------------------

    async def _general_page(self, s) -> list[discord.ui.Item]:
        items: list[discord.ui.Item] = []
        for field, title, hint in (
            ("modlog_channel_id", "Mod-log channel", "Every moderation case is posted here, with History and Undo buttons."),
            ("report_channel_id", "Reports channel", "Member reports arrive here with action buttons."),
            ("alert_channel_id", "Raid alerts channel", "Defaults to the mod-log channel."),
        ):
            current = getattr(s, field)
            items.append(self.text(f"**{title}** · {_channel(self.guild, current)}\n-# {hint}"))
            items.append(self.row(self.channel_select(f"Pick the {title.lower()}", current, self._set_channel(field))))
        items += [
            self.toggle("DM members when punished", s.dm_on_action, self._flip_setting("dm_on_action"),
                        description="Tells them what happened and why, with your appeal link."),
            self.setting("Appeal link", s.appeal_url or "None", "Edit", self.edit_appeal),
        ]
        return items

    async def _automod_page(self, s) -> list[discord.ui.Item]:
        am = s.automod
        items: list[discord.ui.Item] = [
            self.toggle("Automod", am["enabled"], self._flip_filter("enabled"),
                        description="Master switch. Admins and members with Manage Messages are never affected."),
        ]
        for key, title, hint in FILTERS:
            items.append(self.toggle(title, _filter_on(am, key), self._flip_filter(key), description=hint))
        items.append(self.row(
            self.button("Limits", self.edit_limits, emoji="🎚️"),
            self.button(f"Banned words ({len(s.banned_words)})", self.edit_words, emoji="🚫"),
            self.button(f"Allowed links ({len(s.whitelist)})", self.edit_whitelist, emoji="🔗"),
        ))
        return items

    async def _exempt_page(self, s) -> list[discord.ui.Item]:
        am = s.automod
        roles = self.role_select("Roles automod ignores", am["exempt_roles"], self._set_exempt("exempt_roles"), max_values=25)
        channels = discord.ui.ChannelSelect(
            placeholder="Channels automod ignores", min_values=0, max_values=25,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.forum],
            default_values=[discord.SelectDefaultValue(id=c, type=discord.SelectDefaultValueType.channel)
                            for c in am["exempt_channels"]],
        )
        channels.callback = self._set_exempt("exempt_channels")
        return [
            self.text("**Exempt roles**\n-# Members with any of these roles are never checked by automod."),
            self.row(roles),
            self.text("**Exempt channels**\n-# Messages in these channels (and their threads) are never checked."),
            self.row(channels),
        ]

    async def _escalation_page(self, s) -> list[discord.ui.Item]:
        rules = await self.bot.mod_db.list_thresholds(self.guild.id)
        lines = [f"At **{r['warn_count']}** warnings → **{r['action']}**"
                 + (f" for {humanize(r['duration_seconds'])}" if r["duration_seconds"] else "") for r in rules]
        items: list[discord.ui.Item] = [
            self.text("**Automatic punishments**\n-# Applied when a member reaches a number of active warnings.\n\n"
                      + ("\n".join(lines) if lines else "No rules yet.")),
        ]
        if rules:
            remove = discord.ui.Select(placeholder="Remove a rule…", options=[
                discord.SelectOption(label=f"At {r['warn_count']} warnings: {r['action']}", value=str(r["warn_count"]))
                for r in rules[:25]
            ])
            remove.callback = self.remove_rule
            items.append(self.row(remove))
        items.append(self.row(self.button("Add a rule", self.add_rule, emoji="➕", style_=discord.ButtonStyle.success)))
        return items

    async def _raid_page(self, s) -> list[discord.ui.Item]:
        raid = s.raid
        age = raid["min_account_age_days"]
        return [
            self.toggle("Join-flood detection", raid["enabled"], self._flip_raid,
                        description="Turns raid mode on automatically when lots of accounts join at once."),
            self.setting("Join flood", f"{raid['joins']} joins within {raid['seconds']}s", "Edit", self.edit_flood),
            self.setting("Minimum account age", f"{age} day(s): younger accounts are kicked" if age else "Off",
                         "Edit", self.edit_age),
            discord.ui.Separator(),
            self.toggle("🚨 Raid mode", s.raidmode_active, self.toggle_raidmode,
                        description="Locks every text channel and kicks anyone who joins. Asks before changing."),
        ]

    # --- callbacks ----------------------------------------------------------------

    def _goto(self, page: Page):
        async def callback(interaction: discord.Interaction) -> None:
            self.page = page
            await self.rerender(interaction, changed=False)
        return callback

    def _set_channel(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            values = interaction.data.get("values") or []
            await self.bot.mod_configs.update(self.guild.id, **{field: int(values[0]) if values else None})
            await self.rerender(interaction)
        return callback

    def _flip_setting(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            s = await self.bot.mod_configs.get(self.guild.id)
            await self.bot.mod_configs.update(self.guild.id, **{field: not getattr(s, field)})
            await self.rerender(interaction)
        return callback

    async def _automod(self) -> dict:
        # Copy so a failed save can't leave the cached settings modified.
        return copy.deepcopy((await self.bot.mod_configs.get(self.guild.id)).automod)

    def _flip_filter(self, key: str):
        async def callback(interaction: discord.Interaction) -> None:
            am = await self._automod()
            if isinstance(am[key], dict):
                am[key]["enabled"] = not am[key]["enabled"]
            else:
                am[key] = not am[key]
            await self.bot.mod_configs.update(self.guild.id, automod=am)
            await self.rerender(interaction)
        return callback

    def _set_exempt(self, key: str):
        async def callback(interaction: discord.Interaction) -> None:
            am = await self._automod()
            am[key] = [int(v) for v in interaction.data.get("values") or []]
            await self.bot.mod_configs.update(self.guild.id, automod=am)
            await self.rerender(interaction)
        return callback

    async def edit_appeal(self, interaction: discord.Interaction) -> None:
        s = await self.bot.mod_configs.get(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            url = values[0] or None
            if url and not url.startswith(("http://", "https://")):
                raise ValueError("The appeal link must start with http:// or https://.")
            await self.bot.mod_configs.update(self.guild.id, appeal_url=url)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Appeal link", [
            Field("Link", s.appeal_url, placeholder="https://forms.gle/…", max_length=500,
                  description="Shown as a button in punishment DMs. Leave empty for none."),
        ], save))

    async def edit_limits(self, interaction: discord.Interaction) -> None:
        am = (await self.bot.mod_configs.get(self.guild.id)).automod

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            new = await self._automod()
            new["spam"].update(messages=_number(values[0], "Spam messages", 2, 30), seconds=_number(values[1], "Spam seconds", 1, 60))
            new["duplicates"].update(count=_number(values[2], "Repeated messages", 2, 20),
                                     seconds=_number(values[3], "Repeat window", 5, 600))
            new["mentions"]["max"] = _number(values[4], "Mentions", 1, 50)
            await self.bot.mod_configs.update(self.guild.id, automod=new)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Automod limits", [
            Field("Spam: messages allowed…", str(am["spam"]["messages"]), max_length=2, required=True, description="2–30"),
            Field("…within seconds", str(am["spam"]["seconds"]), max_length=2, required=True, description="1–60"),
            Field("Repeats: identical messages…", str(am["duplicates"]["count"]), max_length=2, required=True, description="2–20"),
            Field("…within seconds", str(am["duplicates"]["seconds"]), max_length=3, required=True, description="5–600"),
            Field("Max mentions in one message", str(am["mentions"]["max"]), max_length=2, required=True, description="1–50"),
        ], save))

    async def edit_words(self, interaction: discord.Interaction) -> None:
        current = await self.bot.mod_db.get_banned_words(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            wanted = {w.strip().lower() for w in values[0].splitlines() if w.strip()}
            if any(len(w) > 100 for w in wanted):
                raise ValueError("Each word or phrase can be at most 100 characters.")
            for word in wanted - set(current):
                await self.bot.mod_db.add_banned_word(self.guild.id, word)
            for word in set(current) - wanted:
                await self.bot.mod_db.remove_banned_word(self.guild.id, word)
            self.bot.mod_configs.invalidate(self.guild.id)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Banned words", [
            Field("Words or phrases, one per line", "\n".join(current), paragraph=True, max_length=4000,
                  description="Matched as whole words, ignoring case."),
        ], save))

    async def edit_whitelist(self, interaction: discord.Interaction) -> None:
        current = await self.bot.mod_db.get_whitelist(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            wanted = set()
            for line in values[0].splitlines():
                if not line.strip():
                    continue
                domain = Config._normalize_domain(line)
                if not domain or "." not in domain:
                    raise ValueError(f"“{line.strip()}” isn't a valid domain.")
                wanted.add(domain)
            for domain in wanted - set(current):
                await self.bot.mod_db.add_whitelist(self.guild.id, domain)
            for domain in set(current) - wanted:
                await self.bot.mod_db.remove_whitelist(self.guild.id, domain)
            self.bot.mod_configs.invalidate(self.guild.id)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Allowed link domains", [
            Field("Domains, one per line", "\n".join(current), paragraph=True, max_length=4000,
                  placeholder="youtube.com\ntwitch.tv", description="Subdomains are included. Only used when the Links filter is on."),
        ], save))

    async def add_rule(self, interaction: discord.Interaction) -> None:
        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            warns = _number(values[0], "Warnings", 1, 50)
            action = values[1].strip().lower()
            if action not in ("timeout", "kick", "ban"):
                raise ValueError("The action must be timeout, kick or ban.")
            seconds = None
            if values[2]:
                delta = parse_duration(values[2])
                if delta is None:
                    raise ValueError("Use a duration like 30m, 2h or 7d.")
                seconds = int(delta.total_seconds())
            if action == "timeout" and not seconds:
                raise ValueError("A timeout needs a duration.")
            if action == "timeout" and seconds > MAX_TIMEOUT_SECONDS:
                raise ValueError("Timeouts can be at most 28 days.")
            if action == "kick":
                seconds = None
            await self.bot.mod_db.set_threshold(self.guild.id, warns, action, seconds)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Add an escalation rule", [
            Field("At how many active warnings?", max_length=2, required=True, placeholder="3", description="1–50"),
            Field("Action", max_length=7, required=True, placeholder="timeout, kick or ban"),
            Field("Duration", max_length=10, placeholder="1h, 7d…",
                  description="Needed for timeout. For ban, a duration makes it temporary."),
        ], save))

    async def remove_rule(self, interaction: discord.Interaction) -> None:
        await self.bot.mod_db.delete_threshold(self.guild.id, int(interaction.data["values"][0]))
        await self.rerender(interaction)

    async def _flip_raid(self, interaction: discord.Interaction) -> None:
        raid = copy.deepcopy((await self.bot.mod_configs.get(self.guild.id)).raid)
        raid["enabled"] = not raid["enabled"]
        await self.bot.mod_configs.update(self.guild.id, raid=raid)
        await self.rerender(interaction)

    async def edit_flood(self, interaction: discord.Interaction) -> None:
        raid = (await self.bot.mod_configs.get(self.guild.id)).raid

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            new = copy.deepcopy((await self.bot.mod_configs.get(self.guild.id)).raid)
            new.update(joins=_number(values[0], "Joins", 2, 100), seconds=_number(values[1], "Seconds", 1, 300))
            await self.bot.mod_configs.update(self.guild.id, raid=new)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Join flood", [
            Field("Joins…", str(raid["joins"]), max_length=3, required=True, description="2–100"),
            Field("…within seconds", str(raid["seconds"]), max_length=3, required=True, description="1–300"),
        ], save))

    async def edit_age(self, interaction: discord.Interaction) -> None:
        raid = (await self.bot.mod_configs.get(self.guild.id)).raid

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            new = copy.deepcopy((await self.bot.mod_configs.get(self.guild.id)).raid)
            new["min_account_age_days"] = _number(values[0], "Days", 0, 365)
            await self.bot.mod_configs.update(self.guild.id, raid=new)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Minimum account age", [
            Field("Days", str(raid["min_account_age_days"]), max_length=3, required=True,
                  description="Accounts younger than this are kicked when they join. 0 turns it off."),
        ], save))

    async def toggle_raidmode(self, interaction: discord.Interaction) -> None:
        s = await self.bot.mod_configs.get(self.guild.id)
        turning_on = not s.raidmode_active
        cog = self.bot.get_cog("AntiRaid")
        what = ("Turn raid mode **on**? Every text channel is locked and anyone who joins is kicked."
                if turning_on else "Turn raid mode **off** and unlock the channels it locked?")
        confirm = discord.ui.View(timeout=60)
        go = discord.ui.Button(label="Yes, turn it on" if turning_on else "Yes, turn it off",
                               style=discord.ButtonStyle.danger if turning_on else discord.ButtonStyle.success)
        cancel = discord.ui.Button(label="Cancel")

        async def do_it(confirm_interaction: discord.Interaction) -> None:
            await confirm_interaction.response.edit_message(embed=style.info("Working on it…"), view=None)
            if turning_on:
                await cog.activate(self.guild, "Turned on from the control panel", confirm_interaction.user)
                text = "Raid mode is **on**. Channels are locked and new joins are kicked."
            else:
                await cog.deactivate(self.guild, confirm_interaction.user)
                text = "Raid mode is **off**. Channels are unlocked."
            await confirm_interaction.edit_original_response(embed=style.ok(text))
            await self.bot.panel.refresh(self.guild)

        async def stop(confirm_interaction: discord.Interaction) -> None:
            await confirm_interaction.response.edit_message(embed=style.info("Cancelled. Nothing was changed."), view=None)

        go.callback, cancel.callback = do_it, stop
        confirm.add_item(go)
        confirm.add_item(cancel)
        await interaction.response.send_message(embed=style.warn(what), view=confirm, ephemeral=True)


SECTION = PanelSection(key="moderation", label="Moderation", emoji="🛡️", order=30,
                       status=status, open=ModerationEditor.open)
