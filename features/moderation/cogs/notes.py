import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import embeds


class Notes(commands.Cog):
    """Private staff notes. They are never shown to the user and don't count as warnings."""

    note = app_commands.Group(
        name="note",
        description="Private staff notes on users",
        guild_only=True,
        default_permissions=discord.Permissions(moderate_members=True),
    )

    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.moderate_members:
            raise app_commands.MissingPermissions(["moderate_members"])
        return True

    @note.command(name="add", description="Add a private note to a user")
    async def add(self, interaction: discord.Interaction, user: discord.User,
                  content: app_commands.Range[str, 1, 1000]):
        note = await self.bot.mod_db.add_note(interaction.guild_id, user.id, interaction.user.id, content)
        await interaction.response.send_message(
            embed=embeds.success(f"Note #{note['id']} added for **{user}**."), ephemeral=True)

    @note.command(name="list", description="Show notes on a user")
    async def list_notes(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer(ephemeral=True)
        notes = await self.bot.mod_db.get_notes(interaction.guild_id, user.id)
        embed = embeds.info(f"Notes for {user}", None if notes else "No notes.", interaction.guild)
        for n in notes:
            embed.add_field(
                name=f"#{n['id']} • {discord.utils.format_dt(n['created_at'], 'd')}",
                value=f"{embeds.truncate(n['content'], 900)}\n— <@{n['author_id']}>",
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @note.command(name="delete", description="Delete a note by its number")
    async def delete(self, interaction: discord.Interaction, note_id: int):
        deleted = await self.bot.mod_db.delete_note(interaction.guild_id, note_id)
        embed = embeds.success(f"Note #{note_id} deleted.") if deleted else embeds.error("Note not found.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Notes(bot))
