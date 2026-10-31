import re

import pytest

from core.panel.service import checklist_text
from features.selfroles.buttons import SelfRoleButton
from features.selfroles.db import SelfRolesDB
from features.selfroles.menus import MENUS, PINGS, is_dynamic, ping_menu, plan_toggle

PRONOUNS, DMS = MENUS["pronouns"], MENUS["dms"]


def test_pronouns_add_and_remove_independently():
    assert plan_toggle(PRONOUNS, set(), "she") == (["she"], [])
    assert plan_toggle(PRONOUNS, {"she"}, "they") == (["they"], [])  # several allowed
    assert plan_toggle(PRONOUNS, {"she", "they"}, "she") == ([], ["she"])


def test_dm_status_is_exclusive():
    assert plan_toggle(DMS, {"open"}, "closed") == (["closed"], ["open"])
    assert plan_toggle(DMS, {"closed"}, "closed") == ([], ["closed"])  # press again to clear
    assert plan_toggle(DMS, set(), "ask") == (["ask"], [])


def test_menu_keys_fit_the_button_id():
    pattern = SelfRoleButton.__discord_ui_compiled_template__
    for menu in MENUS.values():
        assert len(menu.options) <= 5 * 5
        for option in menu.options:
            match = re.fullmatch(pattern, f"srole:{menu.key}:{option.key}")
            assert match and match["menu"] == menu.key and match["key"] == option.key
        assert len({o.label.lower() for o in menu.options}) == len(menu.options)


def test_checklist_text():
    text = checklist_text([(True, "Logs"), (False, "Mod-log")])
    assert text.startswith("**Getting started** · 1 left") and "⬜ Mod-log" in text and "✅ Logs" in text
    assert checklist_text([(True, "Logs")]) == "✅ **Getting started: all set.**"


async def test_db(core_db):
    db = SelfRolesDB(core_db.pool)
    assert await db.roles(1, "pronouns") == {} and await db.location(1, "dms") is None
    await db.set_role(1, "pronouns", "she", 10)
    await db.set_role(1, "pronouns", "she", 11)
    assert await db.roles(1, "pronouns") == {"she": 11} and await db.roles(1, "dms") == {}
    await db.set_location(1, "dms", 5, 6)
    await db.set_location(1, "dms", 7, 8)
    assert await db.location(1, "dms") == (7, 8)


def test_ping_menu_from_server_roles():
    menu = ping_menu([(111, "Announcements"), (222, "Events")])
    assert menu.key == "pings" and not menu.exclusive and is_dynamic(menu.key) and not is_dynamic("pronouns")
    assert [(o.key, o.label) for o in menu.options] == [("111", "Announcements"), ("222", "Events")]
    assert plan_toggle(menu, {"111"}, "222") == (["222"], [])  # several allowed
    assert re.fullmatch(SelfRoleButton.__discord_ui_compiled_template__, "srole:pings:1234567890123456789")
    assert PINGS.options == ()


async def test_set_menu_roles_replaces(core_db):
    db = SelfRolesDB(core_db.pool)
    await db.set_menu_roles(1, "pings", [5, 6])
    await db.set_menu_roles(1, "pings", [6, 7])
    assert await db.roles(1, "pings") == {"6": 6, "7": 7}
    await db.set_menu_roles(1, "pings", [])
    assert await db.roles(1, "pings") == {}
