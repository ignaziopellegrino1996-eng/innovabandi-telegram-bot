from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode

from .config import AppConfig
from .db import Database
from .filtering import score_item
from .formatting import format_item, chunk_messages
from .http_client import HttpClient
from .models import Mode, Source, Item
from .sources import fetch_items_for_source, stable_item_id

log = logging.getLogger("runner")


def _pick_sources(all_sources: list[Source], mode: Mode) -> list[Source]:
    return [s for s in all_sources if s.enabled and mode in s.modes]


async def _send_items(bot: Bot, chat_id: int, cfg: AppConfig, items: list[Item]) -> int:
    tz = cfg.tz()
    blocks = [format_item(it, tz) for it in items]
    chunks = chunk_messages(blocks)
    sent = 0
    for msg in chunks:
        await bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        sent += 1
    return sent


async def run_daily_check_once(
    cfg: AppConfig,
    all_sources: list[Source],
    db: Database,
    httpc: HttpClient,
    chat_id: int,
    *,
    mode_override: Optional[Mode] = None,
) -> None:
    settings = await db.get_chat_settings(chat_id)
    mode = mode_override or settings.mode

    bot = Bot(token=cfg.telegram.token_resolved())
    now = datetime.now(cfg.tz())
    started_at = datetime.utcnow().isoformat()

    sources = _pick_sources(all_sources, mode)
    per_source: dict[str, tuple[bool, int, Optional[str]]] = {}
    errors: dict[str, str] = {}
    total_candidates = 0

    new_items: list[Item] = []
    gurs_last_seen_issue = None

    for s in sources:
        try:
            fetched = await fetch_items_for_source(s, httpc, now, gurs_last_seen_issue=gurs_last_seen_issue)
            per_source[s.id] = (True, len(fetched), None)
            total_candidates += len(fetched)

            for it in fetched:
                sr = score_item(cfg.filtering, it.title, it.summary, it.url)
                it2 = Item(**{**it.__dict__, "relevance_score": sr.score, "meta": {**(it.meta or {}), "matched": sr.matched}})
                if not sr.ok:
                    continue

                item_id = stable_item_id(it2.source_id, it2.canonical_url, it2.external_id)
                if await db.has_delivered(chat_id, item_id):
                    continue

                first_seen = datetime.utcnow().isoformat()
                if not await db.has_seen(item_id):
                    await db.upsert_seen_item(item_id, it2, first_seen)

                await db.mark_delivered(chat_id, item_id, first_seen)
                new_items.append(it2)

        except Exception as e:
            err = str(e)
            errors[s.id] = err
            per_source[s.id] = (False, 0, err)
            log.exception("Source failed %s", s.id)

    sent_msgs = 0
    if new_items:
        def _key(x: Item) -> str:
            return x.published or "9999-12-31T00:00:00"
        new_items.sort(key=_key, reverse=True)

        header = f"🆕 <b>Nuovi bandi/avvisi (modalità: {mode.value})</b>\nTotale nuovi: <b>{len(new_items)}</b>"
        await bot.send_message(chat_id=chat_id, text=header, parse_mode=ParseMode.HTML)
        sent_msgs = await _send_items(bot, chat_id, cfg, new_items)
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ Nessuna novità oggi (modalità: <b>{mode.value}</b>).",
            parse_mode=ParseMode.HTML,
        )
        sent_msgs = 1

    finished_at = datetime.utcnow().isoformat()
    error_summary = "; ".join([f"{k}: {v}" for k, v in errors.items()])[:2000]

    await db.mark_run(
        kind="daily",
        chat_id=chat_id,
        mode=mode,
        started_at=started_at,
        finished_at=finished_at,
        total_candidates=total_candidates,
        new_items=len(new_items),
        sent_items=sent_msgs,
        error_summary=error_summary,
        per_source=per_source,
    )


async def run_weekly_report_once(
    cfg: AppConfig,
    all_sources: list[Source],
    db: Database,
    httpc: HttpClient,
    chat_id: int,
    *,
    mode_override: Optional[Mode] = None,
) -> None:
    settings = await db.get_chat_settings(chat_id)
    mode = mode_override or settings.mode

    bot = Bot(token=cfg.telegram.token_resolved())
    started_at = datetime.utcnow().isoformat()

    rows = await db.list_items_for_weekly(chat_id, cfg.weekly.lookback_days)

    counts_by_source: dict[str, int] = {}
    due_soon: list[tuple[str, str, str]] = []
    now = datetime.now(cfg.tz())

    for r in rows:
        sid = str(r["source_id"])
        counts_by_source[sid] = counts_by_source.get(sid, 0) + 1

        ddl = r["deadline"]
        if ddl:
            try:
                dt = datetime.fromisoformat(ddl)
                if dt <= (now.replace(tzinfo=None) + timedelta(days=cfg.weekly.due_soon_days)):
                    due_soon.append((str(r["title"]), ddl, str(r["url"])))
            except Exception:
                pass

    total = len(rows)
    lines = [
        f"📊 <b>Report settimanale</b> (ultimi {cfg.weekly.lookback_days} giorni) • modalità: <b>{mode.value}</b>",
        f"Totale nuovi inviati: <b>{total}</b>",
    ]

    if counts_by_source:
        lines.append("\n<b>Conteggio per fonte</b>:")
        for sid, cnt in sorted(counts_by_source.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"• {sid}: <b>{cnt}</b>")

    if due_soon:
        lines.append(f"\n⏰ <b>In scadenza (entro {cfg.weekly.due_soon_days} giorni)</b>:")
        for t, ddl, url in due_soon[: min(len(due_soon), 15)]:
            try:
                d = datetime.fromisoformat(ddl).strftime("%d/%m/%Y")
            except Exception:
                d = "non indicata"
            lines.append(f"• {t} — <b>{d}</b> — <a href=\"{url}\">link</a>")

    text = "\n".join(lines)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)

    finished_at = datetime.utcnow().isoformat()
    await db.mark_run(
        kind="weekly",
        chat_id=chat_id,
        mode=mode,
        started_at=started_at,
        finished_at=finished_at,
        total_candidates=0,
        new_items=total,
        sent_items=1,
        error_summary="",
        per_source={},
    )
