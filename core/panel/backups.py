"""Control panel section: restore this server's settings from one of the daily backups."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from core import backups, config
from core.access import can_manage
from core.panel.editor import SectionEditor
from core.ui import style

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

SETUP_HELP = ("Restoring needs three settings on the bot's host: `BACKUP_REPO` (the GitHub repo that runs the backup "
              "workflow, `owner/repo`), `GITHUB_TOKEN` (a token that can read its Actions artifacts) and "
              "`BACKUP_PASSPHRASE` (the one the backups are encrypted with).")


async def status(bot: Bot, guild: discord.Guild) -> str:
    return "Restore settings from a daily backup" if backups.configured() else "Not set up on this bot"


def backup_label(backup: backups.Backup) -> str:
    return f"{backup.created_at:%Y-%m-%d %H:%M} UTC · {backup.size / 1024:,.0f} KB"


def preview_lines(targets, in_backup: dict[str, int | None], now: dict[str, int]) -> list[str]:
    lines = []
    for t in targets:
        count = in_backup.get(t.table)
        backup_text = "not in this backup (left as is)" if count is None else f"{count} in the backup"
        lines.append(f"• **{t.label}**: {backup_text} · {now.get(t.table, 0)} now")
    return lines


class BackupsEditor(SectionEditor):
    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int) -> None:
        super().__init__(bot, guild, author_id)
        self.backups: list[backups.Backup] | None = None
        self.chosen: backups.Backup | None = None
        self.data: dict[str, backups.TableData] | None = None
        self.now: dict[str, int] = {}
        self.error: str | None = None

    async def build(self) -> None:
        items: list[discord.ui.Item] = [self.text(
            "## 💾 Backups\nPut this server's settings back the way they were in one of the daily backups "
            "(kept for 30 days). Only settings change: levels, cases, tickets and other records stay as they are.")]
        if not backups.configured():
            items += [discord.ui.Separator(), self.text(f"{style.WARN} {SETUP_HELP}")]
        elif not await can_manage(self.bot, discord.Object(self.author_id), self.guild):
            items += [discord.ui.Separator(), self.text("-# Only the server owner can restore settings.")]
        else:
            items += await self._restore_items()
        if self.error:
            items.append(self.text(f"{style.FAIL} {self.error}"))
        self.add_item(self.container(*items))

    async def _restore_items(self) -> list[discord.ui.Item]:
        items: list[discord.ui.Item] = [discord.ui.Separator()]
        if self.backups is None:
            items.append(self.row(self.button("Show backups", self.load_list, emoji="📂", style_=discord.ButtonStyle.primary)))
            return items
        if not self.backups:
            items.append(self.text("No backups found. Run **Actions → Database backup** on GitHub to make one."))
            return items
        items.append(self.row(self.choice_select(
            "Pick a backup…", [(str(b.id), backup_label(b)) for b in self.backups],
            str(self.chosen.id) if self.chosen else None, self.pick)))
        if self.chosen and self.data is not None:
            targets = self.bot.restore_targets
            in_backup = {t.table: len(self.data[t.table].for_guild(self.guild.id)) if t.table in self.data else None
                         for t in targets}
            items += [
                self.text(f"**Backup from {backup_label(self.chosen)}** · rows for this server\n"
                          + "\n".join(preview_lines(targets, in_backup, self.now))),
                self.row(self.button("Restore these settings", self.ask_restore, emoji="♻️", style_=discord.ButtonStyle.danger)),
            ]
        return items

    async def load_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            self.backups, self.error = await backups.list_backups(), None
        except Exception as exc:
            log.warning("Listing backups failed: %s", exc)
            self.error = str(exc) if isinstance(exc, RuntimeError) else "Couldn't reach GitHub. Try again in a moment."
        await self.rerender(interaction, changed=False)

    async def pick(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        chosen = next((b for b in self.backups or [] if str(b.id) == interaction.data["values"][0]), None)
        self.chosen, self.data, self.error = chosen, None, None
        if chosen is not None:
            try:
                encrypted = await backups.download(chosen)
                tables = {t.table for t in self.bot.restore_targets}
                self.data = await asyncio.to_thread(backups.read_backup, encrypted, config.BACKUP_PASSPHRASE, tables)
                self.now = await backups.current_counts(self.bot.db.pool, self.bot.restore_targets, self.guild.id)
            except Exception as exc:
                log.warning("Reading backup %s failed: %s", chosen.name, exc)
                self.error = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "Couldn't read that backup."
        await self.rerender(interaction, changed=False)

    async def ask_restore(self, interaction: discord.Interaction) -> None:
        if not await can_manage(self.bot, interaction.user, self.guild) or self.data is None or self.chosen is None:
            await interaction.response.send_message(embed=style.fail("Only the server owner can restore settings."),
                                                    ephemeral=True)
            return
        chosen, data = self.chosen, self.data
        confirm = discord.ui.View(timeout=60)
        go = discord.ui.Button(label="Yes, restore", style=discord.ButtonStyle.danger)
        cancel = discord.ui.Button(label="Cancel")

        async def do_it(confirm_interaction: discord.Interaction) -> None:
            await confirm_interaction.response.edit_message(embed=style.info("Restoring…"), view=None)
            try:
                restored = await backups.restore(self.bot.db.pool, self.bot.restore_targets, data, self.guild.id)
            except Exception as exc:
                log.exception("Restoring settings for guild %s failed", self.guild.id)
                await confirm_interaction.edit_original_response(
                    embed=style.fail(f"Nothing was changed: the restore failed ({exc})."))
                return
            log.info("Guild %s settings restored from %s by %s", self.guild.id, chosen.name, confirm_interaction.user.id)
            await confirm_interaction.edit_original_response(embed=style.ok(
                f"Settings restored from the backup of {backup_label(chosen)} ({sum(restored.values())} rows). "
                "Re-post the ticket, self-role or panel messages if they've changed since."))
            await self.bot.panel.refresh(self.guild)

        async def stop(confirm_interaction: discord.Interaction) -> None:
            await confirm_interaction.response.edit_message(embed=style.info("Cancelled. Nothing was changed."), view=None)

        go.callback, cancel.callback = do_it, stop
        confirm.add_item(go)
        confirm.add_item(cancel)
        await interaction.response.send_message(
            embed=style.warn(f"Replace this server's settings with the ones from {backup_label(chosen)}? "
                             "Settings changed since then are lost."), view=confirm, ephemeral=True)
