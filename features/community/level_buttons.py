""""📊 My rank" and "🏆 Leaderboard" buttons under level-up messages.

DynamicItems (custom_ids "lvl:rank" / "lvl:board"), so buttons on old messages keep working after
restarts. Whoever clicks gets their own answer, privately.
"""

from __future__ import annotations

import discord

from features.community.services.messages import add_link_buttons

NOT_ENABLED = "Leveling isn't turned on in this server."


async def _leveling_enabled(interaction: discord.Interaction) -> bool:
    config = await interaction.client.guild_config.get(interaction.guild_id)
    if not config.leveling_enabled:
        await interaction.response.send_message(NOT_ENABLED, ephemeral=True)
        return False
    return True


class RankButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lvl:rank"):
    def __init__(self) -> None:
        super().__init__(discord.ui.Button(label="My rank", emoji="📊", style=discord.ButtonStyle.secondary,
                                           custom_id="lvl:rank"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> RankButton:
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        from features.community.cogs.leveling import rank_card, rank_embed

        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member) or not await _leveling_enabled(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        info = await interaction.client.leveling.rank(interaction.guild.id, member.id)
        card = await rank_card(member, info)
        if card is not None:
            await interaction.followup.send(file=card, ephemeral=True)
        else:
            await interaction.followup.send(embed=rank_embed(member, info), ephemeral=True)


class LeaderboardButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lvl:board"):
    def __init__(self) -> None:
        super().__init__(discord.ui.Button(label="Leaderboard", emoji="🏆", style=discord.ButtonStyle.secondary,
                                           custom_id="lvl:board"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> LeaderboardButton:
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        from features.community.cogs.leaderboard import LeaderboardView

        guild = interaction.guild
        if guild is None or not await _leveling_enabled(interaction):
            return
        cog = interaction.client.get_cog("LeaderboardCog")
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed, pages = await cog.build_page(guild, 0, interaction.user.id)
        if pages <= 1:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        view = LeaderboardView(cog, interaction.user.id, 0, pages)
        view.message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)


def level_up_view(links: list[dict[str, str]]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RankButton())
    view.add_item(LeaderboardButton())
    return add_link_buttons(view, links, row=1)
