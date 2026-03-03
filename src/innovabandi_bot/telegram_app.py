from __future__ import annotations

import logging
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import AppConfig
from .db import Database
from .http_client import HttpClient
from .models import Mode
from .runner import run_daily_check_once, run_weekly_report_once

log = logging.getLogger("telegram_app")


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_chat or not update.effective_user:
        return False
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat
    cfg: AppConfig = context.application.bot_data["cfg"]
    srcs = context.application.bot_data["sources"]
    db_path: Path = context.application.bot_data["db_path"]

    async with Database(db_path) as db:
        await db.init()
        async with HttpClient(cfg.http) as httpc:
            await db.ensure_chat(update.effective_chat.id, default_mode=cfg.modes.default_mode)
            await run_daily_check_once(cfg, srcs, db, httpc, update.effective_chat.id)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat
    cfg: AppConfig = context.application.bot_data["cfg"]
    db_path: Path = context.application.bot_data["db_path"]

    async with Database(db_path) as db:
        await db.init()
        await db.ensure_chat(update.effective_chat.id, default_mode=cfg.modes.default_mode)
        settings = await db.get_chat_settings(update.effective_chat.id)

        last = await db.list_last_run(update.effective_chat.id)
        if not last:
            await update.message.reply_text("Nessun run registrato ancora.")
            return

        run_id = int(last["run_id"])
        rs = await db.list_last_run_sources(run_id)

        lines = [
            "📍 <b>Status</b>",
            f"Modalità: <b>{settings.mode.value}</b>",
            f"Ultimo run: <b>{last['kind']}</b> • finito: <b>{last['finished_at']}</b>",
            f"Nuovi: <b>{last['new_items']}</b> • Candidati: <b>{last['total_candidates']}</b>",
        ]
        if last["error_summary"]:
            lines.append(f"⚠️ Errori: <code>{last['error_summary']}</code>")

        if rs:
            lines.append("\n<b>Esito fonti</b>:")
            for r in rs[:30]:
                ok = "✅" if int(r["ok"]) == 1 else "❌"
                lines.append(f"{ok} {r['source_id']} (fetched: {r['fetched_count']})")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat
    cfg: AppConfig = context.application.bot_data["cfg"]
    srcs = context.application.bot_data["sources"]
    db_path: Path = context.application.bot_data["db_path"]

    async with Database(db_path) as db:
        await db.init()
        await db.ensure_chat(update.effective_chat.id, default_mode=cfg.modes.default_mode)
        settings = await db.get_chat_settings(update.effective_chat.id)

    active = [s for s in srcs if s.enabled and settings.mode in s.modes]
    lines = [f"📚 <b>Fonti attive</b> (modalità: <b>{settings.mode.value}</b>)"]
    for s in active:
        lines.append(f"• <b>{s.id}</b> — {s.level} — <code>{s.kind}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat
    if not await _is_admin(update, context):
        await update.message.reply_text("Solo admin del gruppo possono cambiare modalità.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /mode full | /mode regioni")
        return

    wanted = context.args[0].lower().strip()
    if wanted not in ("full", "regioni"):
        await update.message.reply_text("Valori ammessi: full | regioni")
        return

    cfg: AppConfig = context.application.bot_data["cfg"]
    db_path: Path = context.application.bot_data["db_path"]

    async with Database(db_path) as db:
        await db.init()
        await db.ensure_chat(update.effective_chat.id, default_mode=cfg.modes.default_mode)
        await db.set_chat_mode(update.effective_chat.id, Mode(wanted))

    await update.message.reply_text(f"✅ Modalità impostata su: <b>{wanted}</b>", parse_mode=ParseMode.HTML)


async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat
    cfg: AppConfig = context.application.bot_data["cfg"]
    allow = set(cfg.telegram.weekly_allowlist_user_ids or [])
    uid = update.effective_user.id if update.effective_user else None

    if uid not in allow and not await _is_admin(update, context):
        await update.message.reply_text("Solo admin (o allowlist) possono usare /weekly.")
        return

    srcs = context.application.bot_data["sources"]
    db_path: Path = context.application.bot_data["db_path"]

    async with Database(db_path) as db:
        await db.init()
        async with HttpClient(cfg.http) as httpc:
            await db.ensure_chat(update.effective_chat.id, default_mode=cfg.modes.default_mode)
            await run_weekly_report_once(cfg, srcs, db, httpc, update.effective_chat.id)


async def _job_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: AppConfig = context.application.bot_data["cfg"]
    srcs = context.application.bot_data["sources"]
    db_path: Path = context.application.bot_data["db_path"]

    chat_ids = cfg.telegram.chat_ids_resolved()
    async with Database(db_path) as db:
        await db.init()
        async with HttpClient(cfg.http) as httpc:
            for chat_id in chat_ids:
                await db.ensure_chat(chat_id, default_mode=cfg.modes.default_mode)
                await run_daily_check_once(cfg, srcs, db, httpc, chat_id)


async def _job_weekly(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: AppConfig = context.application.bot_data["cfg"]
    srcs = context.application.bot_data["sources"]
    db_path: Path = context.application.bot_data["db_path"]

    chat_ids = cfg.telegram.chat_ids_resolved()
    async with Database(db_path) as db:
        await db.init()
        async with HttpClient(cfg.http) as httpc:
            for chat_id in chat_ids:
                await db.ensure_chat(chat_id, default_mode=cfg.modes.default_mode)
                await run_weekly_report_once(cfg, srcs, db, httpc, chat_id)


async def run_bot_polling(cfg: AppConfig, sources: list, db_path: Path) -> None:
    token = cfg.telegram.token_resolved()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN mancante")

    app = Application.builder().token(token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["sources"] = sources
    app.bot_data["db_path"] = db_path

    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("weekly", cmd_weekly))

    tz = ZoneInfo(cfg.schedule.timezone)
    dh, dm = _parse_hhmm(cfg.schedule.daily_time)
    wh, wm = _parse_hhmm(cfg.schedule.weekly_time)
    weekday_map = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 3, "fri": 5, "sat": 6}
    wday = weekday_map.get(cfg.schedule.weekly_day.lower(), 1)

    app.job_queue.run_daily(_job_daily, time=dtime(dh, dm, tzinfo=tz), name="daily")
    app.job_queue.run_daily(_job_weekly, time=dtime(wh, wm, tzinfo=tz), days=(wday,), name="weekly")

    log.info("Bot avviato. Polling + scheduler attivi (Europe/Rome).")
    await app.run_polling(close_loop=False)
