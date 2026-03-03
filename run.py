#!/usr/bin/env python3
from __future__ import annotations

# FIX: consente import da src/ anche su GitHub Actions senza PYTHONPATH
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import argparse
import asyncio
import logging
import os

from innovabandi_bot.config import load_config, load_sources
from innovabandi_bot.db import Database
from innovabandi_bot.http_client import HttpClient
from innovabandi_bot.runner import run_daily_check_once, run_weekly_report_once
from innovabandi_bot.telegram_app import run_bot_polling
from innovabandi_bot.models import Mode


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Innovabandi Telegram Bot (monitor bandi/avvisi innovazione).")
    p.add_argument("--config", default="config.yaml", help="Path config YAML (default: config.yaml)")
    p.add_argument("--sources", default="sources.yaml", help="Path sources YAML (default: sources.yaml)")
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    p.add_argument("--once", action="store_true", help="Esegue il daily check e termina (per cron/Actions)")
    p.add_argument("--weekly-once", action="store_true", help="Esegue il report settimanale e termina (per cron/Actions)")
    p.add_argument("--expect-local-time", default=None, help="Formato HH:MM. Se non coincide con ora locale, esce senza inviare.")
    p.add_argument("--expect-weekday", default=None, help="mon|tue|wed|thu|fri|sat|sun. Se non coincide, esce senza inviare.")
    p.add_argument("--db-path", default=None, help="Override path SQLite (default da config)")
    p.add_argument("--mode", default=None, help="Override mode in --once/--weekly-once: full|regioni")
    return p.parse_args()


async def _main_async() -> int:
    args = _parse_args()
    _setup_logging(args.log_level)

    cfg = load_config(Path(args.config))
    srcs = load_sources(Path(args.sources))

    db_path = Path(args.db_path) if args.db_path else Path(cfg.db.path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    mode_override = None
    if args.mode:
        mode_override = Mode(args.mode.lower())

    async with Database(db_path) as db:
        await db.init()
        async with HttpClient(cfg.http) as http:
            if args.once or args.weekly_once:
                should_send = cfg.should_run_now(
                    expect_local_time=args.expect_local_time,
                    expect_weekday=args.expect_weekday,
                )
                if not should_send:
                    logging.getLogger("run").info("Skip: non coincide con expect-local-time/weekday.")
                    return 0

                chat_ids = cfg.telegram.chat_ids_resolved()
                if not chat_ids:
                    raise SystemExit("Nessun chat_id configurato. Imposta TELEGRAM_CHAT_ID o config.yaml.")

                for chat_id in chat_ids:
                    await db.ensure_chat(chat_id, default_mode=cfg.modes.default_mode)

                if args.weekly_once:
                    for chat_id in chat_ids:
                        await run_weekly_report_once(cfg, srcs, db, http, chat_id, mode_override=mode_override)
                else:
                    for chat_id in chat_ids:
                        await run_daily_check_once(cfg, srcs, db, http, chat_id, mode_override=mode_override)

                return 0

            # Long-running bot + scheduler
            await run_bot_polling(cfg, srcs, db_path)

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main_async()))


if __name__ == "__main__":
    main()
