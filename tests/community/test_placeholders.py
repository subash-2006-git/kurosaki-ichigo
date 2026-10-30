import pytest

from features.community.utils.placeholders import ordinal, render
from tests.community.fakes import fake_member


@pytest.mark.parametrize(
    "n, expected",
    [
        (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"),
        (21, "21st"), (22, "22nd"), (101, "101st"), (111, "111th"), (112, "112th"), (1282, "1,282nd"),
        (1284, "1,284th"), (0, "0th"),
    ],
)
def test_ordinal(n, expected):
    assert ordinal(n) == expected


def test_render_all_tokens():
    member = fake_member()
    text = render(
        "{user}|{username}|{displayname}|{user_id}|{server}|{membercount}|{membercount_ordinal}", member
    )
    assert text == "<@111>|subash|Subash|111|Lost Island|1,284|1,284th"


def test_unknown_tokens_and_braces_left_alone():
    assert render("Hi {nickname} {} {user", fake_member()) == "Hi {nickname} {} {user"


def test_level_only_when_given():
    member = fake_member()
    assert render("Level {level}", member) == "Level {level}"
    assert render("Level {level}", member, level=10) == "Level 10"


def test_names_are_markdown_escaped():
    member = fake_member(name="cool_guy", display_name="**Boss**", guild_name="My *Server*")
    assert render("{username} {displayname} {server}", member) == r"cool\_guy \*\*Boss\*\* My \*Server\*"


def test_member_count_falls_back_to_cached_members():
    member = fake_member(member_count=None)
    member.guild.members = [object(), object()]
    assert render("{membercount_ordinal}", member) == "2nd"
