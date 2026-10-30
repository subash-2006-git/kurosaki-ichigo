import logging

import discord

log = logging.getLogger(__name__)


async def lock_channel(channel: discord.TextChannel, reason: str) -> bool:
    """Deny @everyone from sending in channel. Returns False if it was already locked."""
    everyone = channel.guild.default_role
    overwrite = channel.overwrites_for(everyone)
    if overwrite.send_messages is False:
        return False
    overwrite.send_messages = False
    overwrite.send_messages_in_threads = False
    overwrite.create_public_threads = False
    await channel.set_permissions(everyone, overwrite=overwrite, reason=reason)
    return True


async def unlock_channel(channel: discord.TextChannel, reason: str) -> None:
    everyone = channel.guild.default_role
    overwrite = channel.overwrites_for(everyone)
    overwrite.send_messages = None
    overwrite.send_messages_in_threads = None
    overwrite.create_public_threads = None
    await channel.set_permissions(everyone, overwrite=None if overwrite.is_empty() else overwrite, reason=reason)


async def lock_server(bot, guild: discord.Guild, reason: str) -> int:
    """Lock every text channel the bot can manage. Remembers which ones it changed."""
    settings = await bot.mod_configs.get(guild.id)
    locked = set(settings.locked_channels)
    count = 0
    for channel in guild.text_channels:
        if not channel.permissions_for(guild.me).manage_roles:
            continue
        try:
            if await lock_channel(channel, reason):
                locked.add(channel.id)
                count += 1
        except discord.HTTPException as exc:
            log.warning("Could not lock #%s in %s: %s", channel.name, guild.id, exc)
    await bot.mod_configs.update(guild.id, locked_channels=list(locked))
    return count


async def unlock_server(bot, guild: discord.Guild, reason: str) -> int:
    """Unlock only the channels lock_server() locked, leaving pre-existing locks alone."""
    settings = await bot.mod_configs.get(guild.id)
    count = 0
    for channel_id in settings.locked_channels:
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            await unlock_channel(channel, reason)
            count += 1
        except discord.HTTPException as exc:
            log.warning("Could not unlock #%s in %s: %s", channel.name, guild.id, exc)
    await bot.mod_configs.update(guild.id, locked_channels=[])
    return count
