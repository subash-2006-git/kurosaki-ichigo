import asyncio
import logging
from types import SimpleNamespace

import discord
import pytest

from features.community.models import RoleMode, XpSource
from features.community.services.guild_config import GuildConfigService
from features.community.services.leveling import AwardResult, LevelingService
from features.community.services.levelups import LevelUpService
from features.community.services.roles import Milestone, RoleService, target_levels

GUILD_ID = 1

# ---------------------------------------------------------------------- fakes


class FakeRole:
    def __init__(self, role_id, name, position, managed=False):
        self.id, self.name, self.position, self.managed = role_id, name, position, managed

    def is_default(self):
        return self.id == GUILD_ID

    def __repr__(self):
        return f"<Role {self.name}>"


class FakeGuild:
    def __init__(self, manage_roles=True, bot_position=50):
        self.id = GUILD_ID
        self.name = "Lost Island"
        self.member_count = 10
        self.members = []
        self._roles = {}
        self._next_id = 1000
        self.created = []
        self.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=manage_roles),
            top_role=FakeRole(2, "Bot", bot_position),
        )

    def add_role(self, name, position=10, **kwargs):
        self._next_id += 1
        role = FakeRole(self._next_id, name, position, **kwargs)
        self._roles[role.id] = role
        return role

    def delete_role(self, role):
        del self._roles[role.id]

    def get_role(self, role_id):
        return self._roles.get(role_id)

    async def create_role(self, *, name, reason=None):
        await asyncio.sleep(0)  # let concurrent syncs interleave
        role = self.add_role(name, position=1)
        self.created.append(role)
        return role


class FakeMember:
    def __init__(self, guild, user_id=10, roles=(), bot=False):
        self.guild, self.id, self.bot = guild, user_id, bot
        self.roles = list(roles)
        self.api_calls = 0
        self.name = self.display_name = f"user{user_id}"
        self.mention = f"<@{user_id}>"
        self.display_avatar = SimpleNamespace(url=f"https://cdn.example/avatars/{user_id}.png")

    async def add_roles(self, *roles, reason=None):
        self.api_calls += 1
        self.roles.extend(roles)

    async def remove_roles(self, *roles, reason=None):
        self.api_calls += 1
        self.roles = [r for r in self.roles if r not in roles]


# ---------------------------------------------------------------------- fixtures


@pytest.fixture
def config_service(db):
    return GuildConfigService(db)


@pytest.fixture
def roles(db, config_service):
    return RoleService(db, config_service)


@pytest.fixture
def guild():
    return FakeGuild()


def names(member):
    return sorted(role.name for role in member.roles)


# ---------------------------------------------------------------------- target_levels


MILESTONES = [Milestone(5, 1), Milestone(10, 2), Milestone(20, 3)]


@pytest.mark.parametrize(
    "level, mode, expected",
    [
        (4, "highest", []),
        (5, "highest", [5]),
        (19, "highest", [10]),
        (99, "highest", [20]),
        (4, "keep_all", []),
        (12, "keep_all", [5, 10]),
        (20, "keep_all", [5, 10, 20]),
    ],
)
def test_target_levels(level, mode, expected):
    assert target_levels(MILESTONES, level, mode) == expected


# ---------------------------------------------------------------------- syncing


async def test_existing_role_given_at_milestone(roles, guild):
    level5 = guild.add_role("VIP")
    await roles.set_milestone(GUILD_ID, 5, level5.id)
    member = FakeMember(guild)
    assert not (await roles.sync_member(member, 4)).changed
    result = await roles.sync_member(member, 5)
    assert result.added == ("VIP",) and names(member) == ["VIP"]


async def test_auto_created_role_is_saved_and_reused(roles, guild):
    await roles.set_milestone(GUILD_ID, 5, None)
    first, second = FakeMember(guild, 10), FakeMember(guild, 11)
    await roles.sync_member(first, 5)
    await roles.sync_member(second, 7)
    assert [r.name for r in guild.created] == ["Level 5"]
    assert names(first) == names(second) == ["Level 5"]
    assert (await roles.milestones(GUILD_ID)) == [Milestone(5, guild.created[0].id)]


async def test_concurrent_level_ups_create_one_role(roles, guild):
    await roles.set_milestone(GUILD_ID, 5, None)
    members = [FakeMember(guild, user_id) for user_id in range(10, 20)]
    await asyncio.gather(*(roles.sync_member(member, 5) for member in members))
    assert len(guild.created) == 1
    assert all(names(member) == ["Level 5"] for member in members)


async def test_highest_mode_removes_lower_level_roles_only(roles, guild):
    l5, l10, other = guild.add_role("L5"), guild.add_role("L10"), guild.add_role("Artist")
    await roles.set_milestone(GUILD_ID, 5, l5.id)
    await roles.set_milestone(GUILD_ID, 10, l10.id)
    member = FakeMember(guild, roles=[l5, other])
    result = await roles.sync_member(member, 12)
    assert (result.added, result.removed) == (("L10",), ("L5",))
    assert names(member) == ["Artist", "L10"]


async def test_keep_all_mode(roles, guild, config_service):
    await config_service.update(GUILD_ID, level_role_mode=RoleMode.KEEP_ALL.value)
    l5, l10 = guild.add_role("L5"), guild.add_role("L10")
    await roles.set_milestone(GUILD_ID, 5, l5.id)
    await roles.set_milestone(GUILD_ID, 10, l10.id)
    member = FakeMember(guild)
    await roles.sync_member(member, 12)
    assert names(member) == ["L10", "L5"]


async def test_level_drop_removes_roles(roles, guild):
    l5 = guild.add_role("L5")
    await roles.set_milestone(GUILD_ID, 5, l5.id)
    member = FakeMember(guild, roles=[l5])
    assert (await roles.sync_member(member, 2)).removed == ("L5",)
    assert member.roles == []


async def test_deleted_role_is_recreated(roles, guild):
    l5 = guild.add_role("L5")
    await roles.set_milestone(GUILD_ID, 5, l5.id)
    guild.delete_role(l5)
    member = FakeMember(guild)
    await roles.sync_member(member, 5)
    assert [r.name for r in guild.created] == ["Level 5"] and names(member) == ["Level 5"]


async def test_no_api_calls_when_roles_already_match(roles, guild):
    l5 = guild.add_role("L5")
    await roles.set_milestone(GUILD_ID, 5, l5.id)
    member = FakeMember(guild, roles=[l5])
    for _ in range(5):
        assert not (await roles.sync_member(member, 7)).changed
    assert member.api_calls == 0


async def test_role_above_bot_reported_and_logged_once(roles, guild, caplog):
    high = guild.add_role("Admin-ish", position=60)  # above the bot's role at 50
    await roles.set_milestone(GUILD_ID, 5, high.id)
    member = FakeMember(guild)
    with caplog.at_level(logging.ERROR, logger="bot.services.roles"):
        first = await roles.sync_member(member, 5)
        await roles.sync_member(member, 5)
    assert "above my highest role" in first.problems[0]
    assert member.roles == [] and member.api_calls == 0
    assert len([r for r in caplog.records if "above my highest role" in r.message]) == 1


async def test_missing_manage_roles_reported(roles):
    guild = FakeGuild(manage_roles=False)
    await roles.set_milestone(GUILD_ID, 5, None)
    result = await roles.sync_member(FakeMember(guild), 5)
    assert "Manage Roles" in result.problems[0] and guild.created == []


async def test_unresolvable_target_keeps_other_level_roles(roles):
    guild = FakeGuild(manage_roles=False)  # can't create the level 10 role
    l5 = guild.add_role("L5")
    await roles.set_milestone(GUILD_ID, 5, l5.id)
    await roles.set_milestone(GUILD_ID, 10, None)
    member = FakeMember(guild, roles=[l5])
    await roles.sync_member(member, 10)
    assert names(member) == ["L5"]


async def test_level_read_from_database_when_not_given(roles, guild, db):
    l5 = guild.add_role("L5")
    await roles.set_milestone(GUILD_ID, 5, l5.id)
    await LevelingService(db).award_xp(GUILD_ID, 10, 2_500, XpSource.ADMIN)  # level 5
    member = FakeMember(guild, 10)
    await roles.sync_member(member)
    assert names(member) == ["L5"]


async def test_bots_and_guilds_without_milestones_are_skipped(roles, guild):
    assert not (await roles.sync_member(FakeMember(guild), 50)).changed
    await roles.set_milestone(GUILD_ID, 5, guild.add_role("L5").id)
    bot_member = FakeMember(guild, bot=True)
    await roles.sync_member(bot_member, 50)
    assert bot_member.roles == []


async def test_removing_milestone_clears_cache(roles, guild):
    await roles.set_milestone(GUILD_ID, 5, guild.add_role("L5").id)
    assert len(await roles.milestones(GUILD_ID)) == 1
    assert await roles.remove_milestone(GUILD_ID, 5)
    assert not await roles.remove_milestone(GUILD_ID, 5)
    assert await roles.milestones(GUILD_ID) == []


# ---------------------------------------------------------------------- level-up announcements


class FakeChannel:
    def __init__(self, guild, can_send=True):
        self.guild = guild
        self.sent = []
        self._can_send = can_send

    def permissions_for(self, _member):
        allowed = self._can_send
        return SimpleNamespace(view_channel=allowed, send_messages=allowed, embed_links=allowed, attach_files=allowed)

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
        self.kwargs = kwargs


@pytest.fixture
def levelups(config_service, roles):
    return LevelUpService(config_service, roles)


async def test_level_up_announced_in_fallback_channel(levelups, guild):
    channel = FakeChannel(guild)
    await levelups.after_award(FakeMember(guild), AwardResult(total_xp=400, old_level=1, new_level=2), channel)
    # Default style: an embed (no text above it) with rank/leaderboard buttons.
    assert channel.sent == [None]
    embed = channel.kwargs["embed"]
    assert embed.title == "🎉 Level up!" and "<@10> just reached **Level 2**" in embed.description
    assert embed.thumbnail.url == "https://cdn.example/avatars/10.png"
    assert [item.custom_id for item in channel.kwargs["view"].children] == ["lvl:rank", "lvl:board"]


async def test_plain_text_level_up(levelups, guild, config_service):
    await config_service.update(GUILD_ID, levelup_use_embed=False)
    channel = FakeChannel(guild)
    await levelups.after_award(FakeMember(guild), AwardResult(total_xp=400, old_level=1, new_level=2), channel)
    assert channel.sent == ["🎉 <@10> reached **Level 2**!"]
    assert "embed" not in channel.kwargs


async def test_no_announcement_without_level_up_or_when_disabled(levelups, guild, config_service):
    channel = FakeChannel(guild)
    await levelups.after_award(FakeMember(guild), AwardResult(total_xp=450, old_level=2, new_level=2), channel)
    await config_service.update(GUILD_ID, levelup_enabled=False)
    await levelups.after_award(FakeMember(guild), AwardResult(total_xp=900, old_level=2, new_level=3), channel)
    assert channel.sent == []


async def test_voice_level_up_without_channel_is_silent(levelups, guild):
    member = FakeMember(guild)
    await levelups.after_award(member, AwardResult(total_xp=400, old_level=1, new_level=2), None)  # no error


async def test_unusable_channels_are_skipped(levelups, guild, config_service):
    await config_service.update(GUILD_ID, levelup_channel_id=999)  # doesn't exist in the guild
    guild.get_channel = lambda _id: None
    blocked = FakeChannel(guild, can_send=False)
    await levelups.after_award(FakeMember(guild), AwardResult(total_xp=400, old_level=1, new_level=2), blocked)
    assert blocked.sent == []


async def test_custom_text_and_roles_after_level_up(levelups, guild, config_service, roles):
    await config_service.update(GUILD_ID, levelup_text="GG {username}, level {level} in {server}!", levelup_use_embed=False)
    await roles.set_milestone(GUILD_ID, 2, guild.add_role("L2").id)
    channel, member = FakeChannel(guild), FakeMember(guild)
    await levelups.after_award(member, AwardResult(total_xp=400, old_level=1, new_level=2), channel)
    assert channel.sent == ["GG user10, level 2 in Lost Island!"]
    assert names(member) == ["L2"]


async def test_failed_announcement_still_syncs_roles(levelups, guild, roles):
    await roles.set_milestone(GUILD_ID, 2, guild.add_role("L2").id)

    class Broken(FakeChannel):
        async def send(self, content=None, **kwargs):
            raise discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "boom")

    member = FakeMember(guild)
    await levelups.after_award(member, AwardResult(total_xp=400, old_level=1, new_level=2), Broken(guild))
    assert names(member) == ["L2"]


# ---------------------------------------------------------------------- hierarchy ties


@pytest.mark.parametrize(
    "role_position, role_id, expected_problem",
    [
        (40, 5000, False),  # clearly below the bot (position 50)
        (60, 5000, True),  # clearly above
        (50, 5000, False),  # same position, newer role (higher id): below the bot's role
        (50, 1, True),  # same position, older role (lower id): above the bot's role
    ],
)
def test_role_problem_breaks_position_ties_like_discord(role_position, role_id, expected_problem):
    from features.community.utils.checks import role_problem

    guild = FakeGuild(bot_position=50)  # bot's top role has id 2
    role = FakeRole(role_id, "Level 1", role_position)
    assert (role_problem(guild, role) is not None) is expected_problem


async def test_freshly_created_role_tied_with_bot_role_is_assigned(roles):
    # Both the bot's role and the new role report position 1, as Discord can right after creation.
    guild = FakeGuild(bot_position=1)
    await roles.set_milestone(GUILD_ID, 1, None)
    member = FakeMember(guild)
    result = await roles.sync_member(member, 10)
    assert result.problems == () and names(member) == ["Level 1"]
