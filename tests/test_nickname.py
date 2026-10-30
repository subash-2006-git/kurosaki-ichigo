import unittest
from types import SimpleNamespace

from features.nickname.db import GuildSettings, NicknameRecord
from features.nickname.services import permission_checks as pc
from features.nickname.services.nickname_planner import (
    SKIP_ALREADY_SET,
    SKIP_CHANGED_SINCE,
    SKIP_NOT_RENAMED,
    plan_rename,
    plan_reset,
)
from features.nickname.services.nickname_templates import TemplateError, render, strip_template, validate_template


class TemplateTests(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(render("★ {name}", "Subash", "subash"), "★ Subash")

    def test_username_and_fixed(self):
        self.assertEqual(render("{name} ({username})", "Sub", "sub_99"), "Sub (sub_99)")
        self.assertEqual(render("Islander", "Subash", "subash"), "Islander")

    def test_long_name_is_truncated(self):
        nick = render("[Lost Island] {name}", "A" * 40, "a")
        self.assertEqual(len(nick), 32)
        self.assertTrue(nick.startswith("[Lost Island] "))

    def test_strip_removes_repeated_prefix(self):
        self.assertEqual(strip_template("★ {name}", "★ ★ Subash"), "Subash")
        self.assertEqual(strip_template("[{name}]", "[[Sub]]"), "Sub")
        self.assertEqual(strip_template("★ {name}", "Subash"), "Subash")
        self.assertEqual(strip_template("★ {name}", "★ "), "★ ")

    def test_invalid_templates(self):
        for bad in ["", "   ", "{nmae}", "x" * 32 + "{name}"]:
            with self.assertRaises(TemplateError, msg=bad):
                validate_template(bad)
        self.assertEqual(validate_template(" ★ {name} "), "★ {name}")


def make_member(member_id=10, nick=None, name="subash", global_name="Subash", bot=False, top=1, roles=()):
    guild = SimpleNamespace(id=1, owner_id=999, me=SimpleNamespace(id=500, top_role=5))
    return SimpleNamespace(
        id=member_id, nick=nick, name=name, global_name=global_name, bot=bot,
        top_role=top, roles=[SimpleNamespace(id=r) for r in roles], guild=guild,
        display_name=nick or global_name or name,
    )


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.settings = GuildSettings(guild_id=1, mode="both", template="★ {name}")

    def test_fresh_member(self):
        d = plan_rename(make_member(), self.settings, None)
        self.assertEqual((d.new_nick, d.original_nick), ("★ Subash", None))

    def test_keeps_custom_nickname_as_base(self):
        d = plan_rename(make_member(nick="Sub"), self.settings, None)
        self.assertEqual((d.new_nick, d.original_nick), ("★ Sub", "Sub"))

    def test_reapply_is_idempotent(self):
        record = NicknameRecord(original_nick="Sub", applied_nick="★ Sub")
        d = plan_rename(make_member(nick="★ Sub"), self.settings, record)
        self.assertEqual(d.skip_reason, SKIP_ALREADY_SET)

    def test_manually_applied_template_not_doubled(self):
        d = plan_rename(make_member(nick="★ Subash"), self.settings, None)
        self.assertEqual(d.skip_reason, SKIP_ALREADY_SET)

    def test_template_change_uses_original(self):
        self.settings.template = "[LI] {name}"
        record = NicknameRecord(original_nick="Sub", applied_nick="★ Sub")
        d = plan_rename(make_member(nick="★ Sub"), self.settings, record)
        self.assertEqual(d.new_nick, "[LI] Sub")

    def test_skips(self):
        cases = [
            (make_member(bot=True), pc.SKIP_BOT),
            (make_member(member_id=999), pc.SKIP_OWNER),
            (make_member(top=5), pc.SKIP_ROLE_HIERARCHY),
            (make_member(roles=[42]), pc.SKIP_EXCLUDED_ROLE),
        ]
        self.settings.excluded_roles = {42}
        for member, reason in cases:
            self.assertEqual(plan_rename(member, self.settings, None).skip_reason, reason)
        self.settings.include_bots = True
        self.assertIsNone(plan_rename(make_member(bot=True), self.settings, None).skip_reason)

    def test_reset(self):
        record = NicknameRecord(original_nick=None, applied_nick="★ Subash")
        d = plan_reset(make_member(nick="★ Subash"), self.settings, record)
        self.assertFalse(d.skipped)
        self.assertIsNone(d.new_nick)
        self.assertEqual(plan_reset(make_member(nick="Other"), self.settings, record).skip_reason, SKIP_CHANGED_SINCE)
        self.assertEqual(plan_reset(make_member(), self.settings, None).skip_reason, SKIP_NOT_RENAMED)


if __name__ == "__main__":
    unittest.main()
