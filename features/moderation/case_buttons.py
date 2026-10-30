"""Buttons under mod-log cases: "📜 History" (the user's cases) and "↩️ Undo" (unban / remove timeout).

DynamicItems (custom_ids "case:history:<user id>" and "case:undo:<case number>"), so they keep
working after restarts. Each click checks the clicker's permissions.
"""

from __future__ import annotations

import discord

from core.ui import style
from features.moderation.utils import actions
from features.moderation.utils.embeds import ACTION_ICONS, ACTION_LABELS, info, truncate

UNDOABLE = {"ban": "ban_members", "tempban": "ban_members", "timeout": "moderate_members"}


def case_view(case, *, undone: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(HistoryButton(case["user_id"]))
    if case["action"] in UNDOABLE:
        view.add_item(UndoButton(case["case_num"], disabled=undone))
    return view


def history_lines(cases) -> list[str]:
    lines = []
    for c in cases:
        removed = " ~~removed~~" if c["action"] == "warn" and not c["active"] else ""
        when = discord.utils.format_dt(c["created_at"], "R")
        lines.append(f"`#{c['case_num']}` {ACTION_ICONS.get(c['action'], '📝')} **{ACTION_LABELS.get(c['action'], c['action'])}**"
                     f"{removed} · {when}\n-# {truncate(c['reason'], 90)}")
    return lines


class HistoryButton(discord.ui.DynamicItem[discord.ui.Button], template=r"case:history:(?P<user>\d+)"):
    def __init__(self, user_id: int) -> None:
        super().__init__(discord.ui.Button(label="History", emoji="📜", style=discord.ButtonStyle.secondary,
                                           custom_id=f"case:history:{user_id}"))
        self.user_id = user_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> HistoryButton:
        return cls(int(match["user"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.moderate_members:
            await interaction.response.send_message(embed=style.fail("You need **Timeout Members** to see case history."),
                                                    ephemeral=True)
            return
        cases = await interaction.client.mod_db.get_user_cases(interaction.guild_id, self.user_id, limit=15)
        warns = await interaction.client.mod_db.count_active_warns(interaction.guild_id, self.user_id)
        embed = info("📜 Case history", "\n".join(history_lines(cases)) or "No cases.", interaction.guild)
        embed.description = f"<@{self.user_id}> · **{warns}** active warning{'s' if warns != 1 else ''}\n\n" + embed.description
        embed.set_footer(text="Most recent first · up to 15 cases")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class UndoButton(discord.ui.DynamicItem[discord.ui.Button], template=r"case:undo:(?P<num>\d+)"):
    def __init__(self, case_num: int, *, disabled: bool = False) -> None:
        super().__init__(discord.ui.Button(label="Undo", emoji="↩️", style=discord.ButtonStyle.danger,
                                           custom_id=f"case:undo:{case_num}", disabled=disabled))
        self.case_num = case_num

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> UndoButton:
        return cls(int(match["num"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot, guild, member = interaction.client, interaction.guild, interaction.user
        case = await bot.mod_db.get_case(guild.id, self.case_num)
        if case is None or case["action"] not in UNDOABLE:
            await interaction.response.send_message(embed=style.fail("That case can't be undone."), ephemeral=True)
            return
        permission = UNDOABLE[case["action"]]
        if not isinstance(member, discord.Member) or not getattr(member.guild_permissions, permission):
            needed = "Ban Members" if permission == "ban_members" else "Timeout Members"
            await interaction.response.send_message(embed=style.fail(f"You need **{needed}** to undo this."), ephemeral=True)
            return
        what = "Unban" if case["action"] in ("ban", "tempban") else "Remove the timeout of"
        confirm = ConfirmUndo(member.id)
        await interaction.response.send_message(
            embed=style.warn(f"{what} <@{case['user_id']}> (case #{self.case_num})?"), view=confirm, ephemeral=True
        )
        await confirm.wait()
        if not confirm.confirmed:
            return
        result = await self._undo(bot, guild, member, case)
        await confirm.interaction.response.edit_message(embed=result, view=None)
        if result.colour == style.SUCCESS and interaction.message is not None:
            try:
                await interaction.message.edit(view=case_view(case, undone=True))
            except discord.HTTPException:
                pass

    async def _undo(self, bot, guild: discord.Guild, moderator: discord.Member, case) -> discord.Embed:
        reason = f"Undo of case #{self.case_num}"
        try:
            if case["action"] in ("ban", "tempban"):
                new = await actions.unban(bot, guild, discord.Object(case["user_id"]), moderator, reason)
            else:
                target = guild.get_member(case["user_id"])
                if target is None:
                    return style.warn("They're no longer in the server.")
                if not target.is_timed_out():
                    return style.warn("They aren't timed out anymore.")
                new = await actions.untimeout(bot, guild, target, moderator, reason)
        except discord.NotFound:
            return style.warn("They aren't banned anymore.")
        except discord.Forbidden:
            return style.fail("I don't have permission to do that. Check my role position.")
        return style.ok(f"Done. Logged as case #{new['case_num']}.")


class ConfirmUndo(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.confirmed = False
        self.interaction: discord.Interaction | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="↩️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed, self.interaction = True, interaction
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=style.info("Cancelled. Nothing was changed."), view=None)
        self.stop()
