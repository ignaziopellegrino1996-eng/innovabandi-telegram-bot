from innovabandi_bot.sources import canonicalize_url, stable_item_id


def test_canonicalize_url_removes_utm_and_fragment():
    u = "https://Example.com/path?a=1&utm_source=x#section"
    c = canonicalize_url(u)
    assert "utm_source" not in c
    assert "#" not in c
    assert c.startswith("https://example.com/path")


def test_stable_id_changes_with_external_id():
    base = "https://x.test/a"
    a = stable_item_id("s1", base, None)
    b = stable_item_id("s1", base, "ext1")
    assert a != b
