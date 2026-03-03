from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import Item


def _fmt_date(iso: str | None, tz: ZoneInfo) -> str:
    if not iso:
        return "non indicata"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone(tz).strftime("%d/%m/%Y")
    except Exception:
        return "non indicata"


def format_item(item: Item, tz: ZoneInfo) -> str:
    title = html.escape(item.title)
    level = html.escape(item.level)
    source_id = html.escape(item.source_id)
    source_name = html.escape((item.meta or {}).get("source_name", item.source_id))

    pub = _fmt_date(item.published, tz)
    ddl = _fmt_date(item.deadline, tz)
    summ = html.escape(item.summary)
    url = html.escape(item.url)

    return (
        f"<b>{title}</b>\n"
        f"<i>{level} • {source_name} ({source_id})</i>\n"
        f"📅 Pubblicazione: {pub}\n"
        f"⏳ Scadenza: {ddl}\n"
        f"📝 {summ}\n"
        f"🔗 <a href=\"{url}\">Link</a>"
    )


def chunk_messages(lines: Iterable[str], max_chars: int = 3800) -> list[str]:
    chunks: list[str] = []
    buf = ""
    for block in lines:
        block = block.strip()
        if not block:
            continue
        candidate = (buf + "\n\n" + block).strip() if buf else block
        if len(candidate) > max_chars:
            if buf:
                chunks.append(buf)
                buf = block
            else:
                chunks.append(block[:max_chars])
                buf = ""
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def strip_html_to_plain(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")
