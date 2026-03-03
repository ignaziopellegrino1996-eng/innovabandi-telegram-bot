from __future__ import annotations

import re
from dataclasses import dataclass

from .config import FilteringConfig


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


_CALL_LIKE_WORDS = [
    "bando",
    "avviso",
    "manifestazione di interesse",
    "invito",
    "contribut",
    "finanziament",
    "agevolazion",
    "voucher",
    "misura",
]


def looks_like_call(title: str, summary: str) -> bool:
    t = _norm(f"{title} {summary}")
    return any(w in t for w in _CALL_LIKE_WORDS)


@dataclass(frozen=True)
class ScoreResult:
    score: int
    matched: list[str]
    excluded: list[str]
    ok: bool


def score_item(cfg: FilteringConfig, title: str, summary: str, url: str) -> ScoreResult:
    text = _norm(" ".join([title or "", summary or "", url or ""]))
    matched: list[str] = []
    excluded: list[str] = []

    score = 0
    for kw in cfg.include_keywords:
        k = _norm(kw)
        if k and k in text:
            matched.append(kw)
            score += 2 if len(k) >= 6 else 1

    for ex in cfg.exclude_keywords:
        e = _norm(ex)
        if e and e in text:
            excluded.append(ex)

    ok = (score >= cfg.min_score) and (len(excluded) == 0)
    return ScoreResult(score=score, matched=matched, excluded=excluded, ok=ok)
