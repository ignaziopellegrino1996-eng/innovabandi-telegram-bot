from pathlib import Path

import pytest

from innovabandi_bot.db import Database
from innovabandi_bot.models import Mode, Item


@pytest.mark.asyncio
async def test_db_init_and_chat_settings(tmp_path: Path):
    dbp = tmp_path / "t.sqlite3"
    async with Database(dbp) as db:
        await db.init()
        await db.ensure_chat(-1001, default_mode=Mode.FULL)
        cs = await db.get_chat_settings(-1001)
        assert cs.mode == Mode.FULL

        await db.set_chat_mode(-1001, Mode.REGIONI)
        cs2 = await db.get_chat_settings(-1001)
        assert cs2.mode == Mode.REGIONI


@pytest.mark.asyncio
async def test_seen_and_delivered(tmp_path: Path):
    dbp = tmp_path / "t.sqlite3"
    async with Database(dbp) as db:
        await db.init()
        await db.ensure_chat(1, default_mode=Mode.FULL)

        item = Item(
            source_id="s",
            title="t",
            url="u",
            canonical_url="cu",
            level="Italia",
            published=None,
            deadline=None,
            summary="s",
            relevance_score=5,
        )
        item_id = "abc"
        assert await db.has_seen(item_id) is False
        await db.upsert_seen_item(item_id, item, first_seen="2026-01-01T00:00:00")
        assert await db.has_seen(item_id) is True

        assert await db.has_delivered(1, item_id) is False
        await db.mark_delivered(1, item_id, delivered_at="2026-01-01T00:00:00")
        assert await db.has_delivered(1, item_id) is True
