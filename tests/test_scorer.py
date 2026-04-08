from innovabandi_bot.filtering import score_item
from innovabandi_bot.config import FilteringConfig


def test_score_ok_when_keywords_present():
    cfg = FilteringConfig(
        min_score=3,
        prefetch_detail_if_score_at_least=1,
        max_detail_fetch_per_source=10,
        max_published_age_days=365,
        include_keywords=["innovazione", "pmi", "digitale"],
        exclude_keywords=["concorso"],
    )
    r = score_item(cfg, "Bando innovazione digitale per PMI", "finanzia trasformazione digitale", "https://x")
    assert r.ok is True
    assert r.score >= 3


def test_score_blocked_by_exclude():
    cfg = FilteringConfig(
        min_score=1,
        prefetch_detail_if_score_at_least=1,
        max_detail_fetch_per_source=10,
        max_published_age_days=365,
        include_keywords=["innovazione"],
        exclude_keywords=["concorso"],
    )
    r = score_item(cfg, "Concorso", "innovazione", "https://x")
    assert r.ok is False
    assert "concorso" in [x.lower() for x in r.excluded]
