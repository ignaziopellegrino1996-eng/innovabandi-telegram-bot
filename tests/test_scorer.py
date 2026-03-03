from innovabandi_bot.filtering import score_item
from innovabandi_bot.config import FilteringConfig


def test_score_ok_when_keywords_present():
    cfg = FilteringConfig(
        min_score=3,
        include_keywords=["innovazione", "pmi", "digitale"],
        exclude_keywords=["concorso"],
    )
    r = score_item(cfg, "Bando innovazione digitale per PMI", "finanzia trasformazione digitale", "https://x")
    assert r.ok is True
    assert r.score >= 3


def test_score_blocked_by_exclude():
    cfg = FilteringConfig(
        min_score=1,
        include_keywords=["innovazione"],
        exclude_keywords=["concorso"],
    )
    r = score_item(cfg, "Concorso", "innovazione", "https://x")
    assert r.ok is False
    assert "concorso" in [x.lower() for x in r.excluded]
