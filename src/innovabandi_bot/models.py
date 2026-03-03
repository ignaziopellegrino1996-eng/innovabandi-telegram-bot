from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Mode(str, Enum):
    FULL = "full"
    REGIONI = "regioni"


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    level: str
    kind: str
    url: str
    enabled: bool
    modes: set[Mode]
    parser: Optional[str] = None


@dataclass(frozen=True)
class Item:
    source_id: str
    title: str
    url: str
    canonical_url: str
    level: str
    published: Optional[str]
    deadline: Optional[str]
    summary: str
    external_id: Optional[str] = None
    relevance_score: int = 0
    meta: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class RunStats:
    total_candidates: int
    new_items: int
    sent_items: int
    errors_by_source: dict[str, str]


@dataclass(frozen=True)
class WeeklyStats:
    lookback_days: int
    total_items: int
    counts_by_source: dict[str, int]
    due_soon_count: int
