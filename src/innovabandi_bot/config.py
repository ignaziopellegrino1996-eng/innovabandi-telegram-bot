from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from zoneinfo import ZoneInfo

from .models import Mode, Source

_env_pattern = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def _resolve_env(value: Any) -> Any:
    if isinstance(value, str):
        m = _env_pattern.match(value.strip())
        if m:
            return os.getenv(m.group(1), "")
    return value


def _deep_resolve_env(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_resolve_env(_resolve_env(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve_env(_resolve_env(v)) for v in obj]
    return _resolve_env(obj)


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_ids: list[str]
    weekly_allowlist_user_ids: list[int]

    def token_resolved(self) -> str:
        return os.getenv("TELEGRAM_BOT_TOKEN", "") or self.token

    def chat_ids_resolved(self) -> list[int]:
        env_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        raw = [env_chat] if env_chat else [c for c in self.chat_ids if str(c).strip()]
        out: list[int] = []
        for c in raw:
            s = str(c).strip()
            if s:
                out.append(int(s))
        return out


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: str
    daily_time: str
    weekly_day: str
    weekly_time: str


@dataclass(frozen=True)
class DbConfig:
    path: str


@dataclass(frozen=True)
class HttpConfig:
    timeout_s: float
    max_retries: int
    backoff_base_s: float
    concurrency: int
    rate_limit_rps: float
    user_agent: str


@dataclass(frozen=True)
class FilteringConfig:
    min_score: int
    prefetch_detail_if_score_at_least: int
    max_detail_fetch_per_source: int
    max_published_age_days: int
    include_keywords: list[str]
    exclude_keywords: list[str]


@dataclass(frozen=True)
class WeeklyConfig:
    lookback_days: int
    due_soon_days: int
    max_items: int


@dataclass(frozen=True)
class ModesConfig:
    default_mode: Mode


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig
    schedule: ScheduleConfig
    db: DbConfig
    http: HttpConfig
    filtering: FilteringConfig
    weekly: WeeklyConfig
    modes: ModesConfig

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.schedule.timezone)

    def should_run_now(self, expect_local_time: Optional[str], expect_weekday: Optional[str]) -> bool:
        if not expect_local_time and not expect_weekday:
            return True
        now = datetime.now(self.tz())
        if expect_weekday:
            wd = expect_weekday.strip().lower()
            map_wd = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
            if wd not in map_wd:
                raise ValueError("expect-weekday deve essere mon|tue|wed|thu|fri|sat|sun")
            if now.weekday() != map_wd[wd]:
                return False
        if expect_local_time:
            hh, mm = expect_local_time.split(":")
            expected_minute = int(hh) * 60 + int(mm)
            actual_minute = now.hour * 60 + now.minute
            # GitHub Actions cron può partire con qualche minuto di ritardo;
            # accettiamo una finestra di tolleranza di 15 minuti.
            diff = actual_minute - expected_minute
            if diff < 0 or diff > 15:
                return False
        return True


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        alt = Path("config.example.yaml")
        if alt.exists():
            path = alt
        else:
            raise FileNotFoundError(f"Config non trovato: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = _deep_resolve_env(raw)

    tg = raw.get("telegram", {})
    sc = raw.get("schedule", {})
    db = raw.get("db", {})
    http = raw.get("http", {})
    flt = raw.get("filtering", {})
    wk = raw.get("weekly", {})
    md = raw.get("modes", {})

    return AppConfig(
        telegram=TelegramConfig(
            token=str(tg.get("token", "")).strip(),
            chat_ids=[str(x) for x in (tg.get("chat_ids") or [])],
            weekly_allowlist_user_ids=[int(x) for x in (tg.get("weekly_allowlist_user_ids") or [])],
        ),
        schedule=ScheduleConfig(
            timezone=str(sc.get("timezone", "Europe/Rome")),
            daily_time=str(sc.get("daily_time", "08:00")),
            weekly_day=str(sc.get("weekly_day", "mon")),
            weekly_time=str(sc.get("weekly_time", "08:05")),
        ),
        db=DbConfig(path=str(db.get("path", "data/state.sqlite3"))),
        http=HttpConfig(
            timeout_s=float(http.get("timeout_s", 25)),
            max_retries=int(http.get("max_retries", 3)),
            backoff_base_s=float(http.get("backoff_base_s", 0.6)),
            concurrency=int(http.get("concurrency", 6)),
            rate_limit_rps=float(http.get("rate_limit_rps", 1.2)),
            user_agent=str(http.get("user_agent", "InnovabandiBot/1.1")),
        ),
        filtering=FilteringConfig(
            min_score=int(flt.get("min_score", 3)),
            prefetch_detail_if_score_at_least=int(flt.get("prefetch_detail_if_score_at_least", 1)),
            max_detail_fetch_per_source=int(flt.get("max_detail_fetch_per_source", 15)),
            max_published_age_days=int(flt.get("max_published_age_days", 365)),
            include_keywords=[str(x) for x in (flt.get("include_keywords") or [])],
            exclude_keywords=[str(x) for x in (flt.get("exclude_keywords") or [])],
        ),
        weekly=WeeklyConfig(
            lookback_days=int(wk.get("lookback_days", 7)),
            due_soon_days=int(wk.get("due_soon_days", 10)),
            max_items=int(wk.get("max_items", 40)),
        ),
        modes=ModesConfig(default_mode=Mode(str(md.get("default_mode", "full")).lower())),
    )


def load_sources(path: Path) -> list[Source]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = _deep_resolve_env(raw)
    items = raw.get("sources") or []
    out: list[Source] = []
    for s in items:
        modes = {Mode(m.lower()) for m in (s.get("modes") or ["full"])}
        out.append(
            Source(
                id=str(s["id"]),
                name=str(s["name"]),
                level=str(s.get("level", "")),
                kind=str(s["kind"]),
                url=str(s["url"]),
                enabled=bool(s.get("enabled", True)),
                modes=modes,
                parser=s.get("parser"),
            )
        )
    return out
