import unittest
from types import SimpleNamespace

from core.ui.embed_draft import (
    COLOUR_NAMES,
    DraftError,
    EmbedDraft,
    allowed_mentions,
    parse_colour,
    parse_yes_no,
    validate_url,
)


class ParsingTests(unittest.TestCase):
    def test_colours(self):
        for text in ["#5865F2", "5865f2", "0x5865F2", " blurple "]:
            self.assertEqual(parse_colour(text), 0x5865F2, text)
        self.assertIsNone(parse_colour(""))
        self.assertEqual(parse_colour("#000000"), COLOUR_NAMES["black"])
        for bad in ["#12345", "notacolour", "#GGGGGG"]:
            with self.assertRaises(DraftError, msg=bad):
                parse_colour(bad)

    def test_yes_no(self):
        self.assertTrue(parse_yes_no("Yes"))
        self.assertFalse(parse_yes_no("off"))
        self.assertTrue(parse_yes_no("", default=True))
        with self.assertRaises(DraftError):
            parse_yes_no("maybe")

    def test_urls(self):
        self.assertEqual(validate_url(" https://x.com/a.png ", "Image"), "https://x.com/a.png")
        self.assertIsNone(validate_url("", "Image"))
        for bad in ["x.com/a.png", "ftp://x.com", "https://has space.com"]:
            with self.assertRaises(DraftError, msg=bad):
                validate_url(bad, "Image")


class DraftTests(unittest.TestCase):
    def test_empty(self):
        draft = EmbedDraft(colour=0xFF0000, timestamp=True)
        self.assertTrue(draft.is_empty())
        draft.image_url = "https://x.com/a.png"
        self.assertFalse(draft.is_empty())

    def test_fields(self):
        draft = EmbedDraft()
        for i in range(25):
            draft.add_field(f"n{i}", "v", inline=True)
        with self.assertRaises(DraftError):
            draft.add_field("one", "too many", inline=False)
        draft.remove_field("1")
        self.assertEqual(draft.fields[0].name, "n1")
        for bad in ["0", "25", "abc"]:
            with self.assertRaises(DraftError, msg=bad):
                draft.remove_field(bad)
        with self.assertRaises(DraftError):
            draft.add_field("  ", "value", inline=False)

    def test_total_length_limit(self):
        draft = EmbedDraft(description="a" * 4000)
        draft.add_field("n", "b" * 1000, inline=False)
        draft.check()
        draft.add_field("n", "c" * 1000, inline=False)
        with self.assertRaises(DraftError):
            draft.check()

    def test_dependent_parts_need_their_text(self):
        for draft in [EmbedDraft(url="https://x.com"), EmbedDraft(author_icon="https://x.com/i.png"),
                      EmbedDraft(footer_icon="https://x.com/i.png")]:
            with self.assertRaises(DraftError):
                draft.check()

    def test_to_embed(self):
        draft = EmbedDraft(title="Hi", url="https://x.com", description="Text", colour=0x5865F2,
                           footer_text="Foot", timestamp=True)
        draft.add_field("A", "1", inline=True)
        embed = draft.to_embed()
        self.assertEqual((embed.title, embed.url, embed.colour.value), ("Hi", "https://x.com", 0x5865F2))
        self.assertEqual((embed.footer.text, len(embed.fields)), ("Foot", 1))
        self.assertIsNotNone(embed.timestamp)

    def test_message_content(self):
        draft = EmbedDraft()
        self.assertIsNone(draft.message_content())
        draft.message = "New event!"
        draft.ping_everyone = True
        draft.mention_role_ids = [1]
        draft.mention_user_ids = [2]
        self.assertEqual(draft.message_content(), "@everyone <@&1> <@2>\nNew event!")
        draft.message = "x" * 2000
        with self.assertRaises(DraftError):
            draft.check()


def role(name, mentionable):
    return SimpleNamespace(name=name, mentionable=mentionable)


class AllowedMentionsTests(unittest.TestCase):
    def test_nothing_selected_pings_nobody(self):
        mentions = allowed_mentions(EmbedDraft(), {}, False, False)
        self.assertEqual(mentions.to_dict()["parse"], [])
        self.assertFalse(mentions.everyone)

    def test_mentionable_roles_and_users_need_no_permission(self):
        draft = EmbedDraft(mention_role_ids=[1], mention_user_ids=[2])
        data = allowed_mentions(draft, {1: role("Events", True)}, False, False).to_dict()
        self.assertEqual((data["roles"], data["users"]), ([1], [2]))

    def test_restricted_pings_need_both_permissions(self):
        cases = [EmbedDraft(ping_everyone=True), EmbedDraft(ping_here=True), EmbedDraft(mention_role_ids=[1])]
        roles = {1: role("Staff", False)}
        for draft in cases:
            with self.assertRaisesRegex(DraftError, "You need"):
                allowed_mentions(draft, roles, False, True)
            with self.assertRaisesRegex(DraftError, "I need"):
                allowed_mentions(draft, roles, True, False)
            allowed_mentions(draft, roles, True, True)
        self.assertTrue(allowed_mentions(EmbedDraft(ping_here=True), {}, True, True).everyone)

    def test_deleted_role(self):
        with self.assertRaises(DraftError):
            allowed_mentions(EmbedDraft(mention_role_ids=[9]), {9: None}, True, True)


if __name__ == "__main__":
    unittest.main()
