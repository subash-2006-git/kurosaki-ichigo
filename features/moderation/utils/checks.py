import discord


def bot_hierarchy_error(guild: discord.Guild, target: discord.abc.User) -> str | None:
    """Return why the bot can't act on target, or None if it can."""
    if not isinstance(target, discord.Member):
        return None
    if target.id == guild.me.id:
        return "I can't moderate myself."
    if target.id == guild.owner_id:
        return "I can't moderate the server owner."
    if target.top_role >= guild.me.top_role:
        return f"My highest role must be above {target.mention}'s highest role."
    return None


def hierarchy_error(actor: discord.Member, target: discord.abc.User) -> str | None:
    """Return why actor (with the bot's help) can't act on target, or None if they can."""
    if not isinstance(target, discord.Member):
        return None
    guild = actor.guild
    if target.id == actor.id:
        return "You can't moderate yourself."
    if target.id == guild.owner_id:
        return "You can't moderate the server owner."
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return f"You can't moderate {target.mention}: their highest role is equal to or above yours."
    return bot_hierarchy_error(guild, target)


def role_error(actor: discord.Member, role: discord.Role) -> str | None:
    """Return why actor can't assign/remove role, or None if they can."""
    guild = actor.guild
    if role.is_default() or role.managed:
        return f"{role.mention} is managed by Discord or an integration and can't be assigned."
    if actor.id != guild.owner_id and role >= actor.top_role:
        return f"{role.mention} is equal to or above your highest role."
    if role >= guild.me.top_role:
        return f"{role.mention} is equal to or above my highest role."
    return None
