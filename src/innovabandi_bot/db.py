from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite

from .models import Mode, Item

log = logging.getLogger("db")


@dataclass(frozen=True)
class ChatSettings:
    chat_id: int
    mode: Mode
    last_run_at: Optional[str]
    last_weekly_sent_at: Optional[str]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def __aenter__(self) -> "Database":
        self._conn = await aiosqlite.connect(self.path.as_posix())
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("DB non inizializzato")
        return self._conn

    async def init(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
              item_id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              url TEXT NOT NULL,
              canonical_url TEXT NOT NULL,
              title TEXT NOT NULL,
              level TEXT NOT NULL,
              published TEXT,
              deadline TEXT,
              first_seen TEXT NOT NULL,
              summary TEXT NOT NULL,
              relevance_score INTEGER NOT NULL DEFAULT 0,
              meta_json TEXT
            );

            CREATE TABLE IF NOT EXISTS delivered_items (
              chat_id INTEGER NOT NULL,
              item_id TEXT NOT NULL,
              delivered_at TEXT NOT NULL,
              PRIMARY KEY (chat_id, item_id),
              FOREIGN KEY (item_id) REFERENCES seen_items(item_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chat_settings (
              chat_id INTEGER PRIMARY KEY,
              mode TEXT NOT NULL,
              last_run_at TEXT,
              last_weekly_sent_at TEXT,
              flags_json TEXT
            );

            CREATE TABLE IF NOT EXISTS runs (
              run_id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              chat_id INTEGER NOT NULL,
              mode TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              total_candidates INTEGER NOT NULL,
              new_items INTEGER NOT NULL,
              sent_items INTEGER NOT NULL,
              error_summary TEXT
            );

            CREATE TABLE IF NOT EXISTS run_sources (
              run_id INTEGER NOT NULL,
              source_id TEXT NOT NULL,
              ok INTEGER NOT NULL,
              fetched_count INTEGER NOT NULL,
              error TEXT,
              PRIMARY KEY (run_id, source_id),
              FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            """
        )
        await self.conn.commit()

    async def ensure_chat(self, chat_id: int, default_mode: Mode) -> None:
        cur = await self.conn.execute("SELECT chat_id FROM chat_settings WHERE chat_id = ?", (chat_id,))
        row = await cur.fetchone()
        if row:
            return
        await self.conn.execute(
            "INSERT INTO chat_settings(chat_id, mode, last_run_at, last_weekly_sent_at, flags_json) VALUES(?,?,?,?,?)",
            (chat_id, default_mode.value, None, None, json.dumps({})),
        )
        await self.conn.commit()

    async def get_chat_settings(self, chat_id: int) -> ChatSettings:
        cur = await self.conn.execute(
            "SELECT chat_id, mode, last_run_at, last_weekly_sent_at FROM chat_settings WHERE chat_id=?",
            (chat_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise RuntimeError(f"chat_settings mancante per chat_id={chat_id}")
        return ChatSettings(
            chat_id=int(row["chat_id"]),
            mode=Mode(str(row["mode"])),
            last_run_at=row["last_run_at"],
            last_weekly_sent_at=row["last_weekly_sent_at"],
        )

    async def set_chat_mode(self, chat_id: int, mode: Mode) -> None:
        await self.conn.execute("UPDATE chat_settings SET mode=? WHERE chat_id=?", (mode.value, chat_id))
        await self.conn.commit()

    async def mark_run(
        self,
        *,
        kind: str,
        chat_id: int,
        mode: Mode,
        started_at: str,
        finished_at: str,
        total_candidates: int,
        new_items: int,
        sent_items: int,
        error_summary: str,
        per_source: dict[str, tuple[bool, int, Optional[str]]],
    ) -> int:
        cur = await self.conn.execute(
            """
            INSERT INTO runs(kind, chat_id, mode, started_at, finished_at, total_candidates, new_items, sent_items, error_summary)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (kind, chat_id, mode.value, started_at, finished_at, total_candidates, new_items, sent_items, error_summary),
        )
        run_id = cur.lastrowid
        for source_id, (ok, fetched_count, err) in per_source.items():
            await self.conn.execute(
                "INSERT INTO run_sources(run_id, source_id, ok, fetched_count, error) VALUES(?,?,?,?,?)",
                (run_id, source_id, 1 if ok else 0, fetched_count, err),
            )

        if kind == "daily":
            await self.conn.execute("UPDATE chat_settings SET last_run_at=? WHERE chat_id=?", (finished_at, chat_id))
        else:
            await self.conn.execute("UPDATE chat_settings SET last_weekly_sent_at=? WHERE chat_id=?", (finished_at, chat_id))

        await self.conn.commit()
        return int(run_id)

    async def has_seen(self, item_id: str) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM seen_items WHERE item_id=?", (item_id,))
        return (await cur.fetchone()) is not None

    async def has_delivered(self, chat_id: int, item_id: str) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM delivered_items WHERE chat_id=? AND item_id=?", (chat_id, item_id))
        return (await cur.fetchone()) is not None

    async def upsert_seen_item(self, item_id: str, item: Item, first_seen: str) -> None:
        meta_json = json.dumps(item.meta or {}, ensure_ascii=False)
        await self.conn.execute(
            """
            INSERT INTO seen_items(item_id, source_id, url, canonical_url, title, level, published, deadline, first_seen, summary, relevance_score, meta_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(item_id) DO NOTHING
            """,
            (
                item_id,
                item.source_id,
                item.url,
                item.canonical_url,
                item.title,
                item.level,
                item.published,
                item.deadline,
                first_seen,
                item.summary,
                int(item.relevance_score),
                meta_json,
            ),
        )

    async def mark_delivered(self, chat_id: int, item_id: str, delivered_at: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO delivered_items(chat_id, item_id, delivered_at) VALUES(?,?,?)",
            (chat_id, item_id, delivered_at),
        )

    async def list_items_for_weekly(self, chat_id: int, lookback_days: int) -> list[aiosqlite.Row]:
        since = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        cur = await self.conn.execute(
            """
            SELECT s.*
            FROM delivered_items d
            JOIN seen_items s ON s.item_id = d.item_id
            WHERE d.chat_id = ? AND d.delivered_at >= ?
            ORDER BY d.delivered_at DESC
            """,
            (chat_id, since),
        )
        return await cur.fetchall()

    async def list_last_run(self, chat_id: int) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM runs WHERE chat_id=? ORDER BY run_id DESC LIMIT 1",
            (chat_id,),
        )
        return await cur.fetchone()

    async def list_last_run_sources(self, run_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM run_sources WHERE run_id=? ORDER BY source_id",
            (run_id,),
        )
        return await cur.fetchall()
