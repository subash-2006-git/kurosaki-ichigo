import discord

SUCCESS = discord.Color.green()
ERROR = discord.Color.red()
INFO = discord.Color.blurple()

ACTION_COLORS = {
    "warn": discord.Color.yellow(),
    "timeout": discord.Color.orange(),
    "untimeout": discord.Color.teal(),
    "kick": discord.Color.dark_orange(),
    "softban": discord.Color.dark_orange(),
    "ban": discord.Color.red(),
    "tempban": discord.Color.red(),
    "unban": discord.Color.green(),
}

ACTION_PAST_TENSE = {
    "warn": "warned",
    "timeout": "timed out",
    "untimeout": "had your timeout removed",
    "kick": "kicked",
    "softban": "kicked",
    "ban": "banned",
    "tempban": "temporarily banned",
    "unban": "unbanned",
}


def success(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {message}", color=SUCCESS)


def error(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=ERROR)


def info(title: str, description: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=INFO)


def truncate(text: str | None, limit: int = 1024) -> str:
    if not text:
        return "No reason provided"
    return text if len(text) <= limit else text[: limit - 1] + "…"
