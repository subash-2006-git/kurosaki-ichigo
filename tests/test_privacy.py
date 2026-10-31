"""/mydata: exactly the deletable rows go, for exactly that user; moderation and ticket records stay."""

from features.privacy import delete_user_data, summary

ME, OTHER = 7, 8


async def seed(conn, user_id: int) -> None:
    await conn.execute("INSERT INTO community.member_levels (guild_id, user_id, total_xp) VALUES (1, $1, 50)", user_id)
    await conn.execute("INSERT INTO community.xp_log (guild_id, user_id, source, amount) VALUES (1, $1, 'message', 5)", user_id)
    await conn.execute("INSERT INTO nickname.original_nicknames (guild_id, user_id, original_nick) VALUES (1, $1, 'x')", user_id)
    await conn.execute("INSERT INTO nickname.change_log (guild_id, user_id, source, result) VALUES (1, $1, 'join', 'changed')", user_id)
    await conn.execute("INSERT INTO starboard.entries (message_id, guild_id, channel_id, author_id, star_message_id, stars) "
                       "VALUES ($1, 1, 2, $2, $3, 3)", 1000 + user_id, user_id, 2000 + user_id)
    await conn.execute("INSERT INTO moderation.cases (guild_id, case_num, user_id, mod_id, action) VALUES (1, $1, $2, 9, 'warn')",
                       user_id, user_id)
    await conn.execute("INSERT INTO moderation.notes (guild_id, user_id, author_id, content) VALUES (1, $1, 9, 'n')", user_id)
    await conn.execute("INSERT INTO tickets.tickets (guild_id, number, channel_id, opener_id) VALUES (1, $1, 5, $2)",
                       user_id, user_id)


async def test_summary_and_delete(core_db):
    async with core_db.pool.acquire() as conn:
        await seed(conn, ME)
        await seed(conn, OTHER)
    deletable, kept = await summary(core_db.pool, ME)
    assert all(n == 1 for n in deletable.values())
    assert kept["Moderation cases"] == 1 and kept["Staff notes"] == 1 and kept["Tickets"] == 1

    posts = await delete_user_data(core_db.pool, ME)
    assert posts == [(1, 2000 + ME)]
    deletable, kept = await summary(core_db.pool, ME)
    assert all(n == 0 for n in deletable.values())
    assert kept["Moderation cases"] == 1 and kept["Staff notes"] == 1 and kept["Tickets"] == 1  # kept

    other, _ = await summary(core_db.pool, OTHER)
    assert all(n == 1 for n in other.values())  # someone else's data untouched
