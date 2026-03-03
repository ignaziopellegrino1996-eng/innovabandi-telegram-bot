from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from pypdf import PdfReader

from .models import Source, Item
from .http_client import HttpClient

log = logging.getLogger("sources")

_UTM_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}

# parole utili per estrarre snippet “centrato” su innovazione
_SNIPPET_HINTS = [
    "innovazione", "digitale", "digitalizzazione", "trasformazione digitale", "transizione digitale",
    "ricerca", "sviluppo", "r&s", "ai", "intelligenza artificiale", "cybersecurity", "cloud",
    "pmi", "startup", "cooperativ", "energia", "circolare"
]


def canonicalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        q = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True) if k not in _UTM_PARAMS]
        q.sort()
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, p.params, urlencode(q), ""))
    except Exception:
        return url


def stable_item_id(source_id: str, canonical_url: str, external_id: Optional[str] = None) -> str:
    base = f"{source_id}::{external_id or canonical_url}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.replace(tzinfo=None).isoformat()


def _try_parse_date(text: str) -> Optional[datetime]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return dtparser.parse(text, dayfirst=True, fuzzy=True)
    except Exception:
        return None


def _extract_first_date_like(s: str) -> Optional[datetime]:
    if not s:
        return None
    patterns = [
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b",
        r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b",
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            try:
                return dtparser.parse(m.group(0), dayfirst=True)
            except Exception:
                pass
    return None


def _shorten(text: str, n: int) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= n:
        return t
    return t[: max(0, n - 1)].rstrip() + "…"


def _make_item(
    source: Source,
    title: str,
    url: str,
    published: Optional[datetime],
    deadline: Optional[datetime],
    summary: str,
    external_id: Optional[str] = None,
    meta: Optional[dict] = None,
) -> Item:
    can = canonicalize_url(url)
    base_meta = {"source_name": source.name}
    if meta:
        base_meta.update(meta)

    return Item(
        source_id=source.id,
        title=_shorten((title or "").strip() or source.name, 200),
        url=url,
        canonical_url=can,
        level=source.level,
        published=_iso_or_none(published),
        deadline=_iso_or_none(deadline),
        summary=_shorten(summary, 250),
        external_id=external_id,
        meta=base_meta,
    )


# ---------- RSS ----------
def _extract_deadline_from_text(text: str) -> Optional[datetime]:
    if not text:
        return None
    low = text.lower()
    for key in ["scadenza", "entro", "termine", "presentazione", "domande entro"]:
        pos = low.find(key)
        if pos != -1:
            window = text[max(0, pos - 120): pos + 240]
            dt = _extract_first_date_like(window)
            if dt:
                return dt
    return None


async def fetch_rss(source: Source, httpc: HttpClient) -> list[Item]:
    raw = await httpc.get_text(source.url)
    feed = feedparser.parse(raw)
    items: list[Item] = []
    for e in feed.entries:
        link = getattr(e, "link", None) or ""
        title = getattr(e, "title", "") or source.name
        summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""
        published_dt = None
        if getattr(e, "published", None):
            published_dt = _try_parse_date(str(e.published))
        elif getattr(e, "updated", None):
            published_dt = _try_parse_date(str(e.updated))

        deadline_dt = _extract_deadline_from_text(summary)
        ext_id = getattr(e, "id", None)

        items.append(
            _make_item(
                source,
                title=title,
                url=link,
                published=published_dt,
                deadline=deadline_dt,
                summary=summary,
                external_id=ext_id,
                meta={"detail_fetchable": True},
            )
        )
    return items


# ---------- HTML ----------
def _soup(html_text: str) -> BeautifulSoup:
    return BeautifulSoup(html_text, "lxml")


def _is_probably_pdf(url: str) -> bool:
    u = (url or "").lower()
    return u.endswith(".pdf") or ".pdf?" in u


def _extract_main_text(soup: BeautifulSoup) -> str:
    # prova selettori “forti”
    for sel in ["main", "article", "#main", ".main", ".content", ".node__content", ".region-content"]:
        node = soup.select_one(sel)
        if node:
            for bad in node.select("script, style, nav, header, footer, form"):
                bad.decompose()
            txt = node.get_text(" ", strip=True)
            if txt and len(txt) > 200:
                return txt

    # fallback: body
    body = soup.body
    if body:
        for bad in body.select("script, style, nav, header, footer, form"):
            bad.decompose()
        return body.get_text(" ", strip=True)

    return soup.get_text(" ", strip=True)


def _extract_published_deadline_from_page_text(text: str) -> tuple[Optional[datetime], Optional[datetime]]:
    pub = None
    ddl = None
    if not text:
        return None, None

    low = text.lower()

    # published hints
    for key in ["pubblicato il", "data pubblicazione", "pubblicazione", "posted on", "published on"]:
        pos = low.find(key)
        if pos != -1:
            window = text[max(0, pos - 80): pos + 160]
            pub = _extract_first_date_like(window) or _try_parse_date(window)
            if pub:
                break

    # deadline hints
    ddl = _extract_deadline_from_text(text)
    return pub, ddl


def _best_snippet(text: str, max_len: int = 250) -> str:
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip()
    low = t.lower()
    best_pos = None
    for k in _SNIPPET_HINTS:
        pos = low.find(k.lower())
        if pos != -1:
            best_pos = pos
            break
    if best_pos is None:
        return _shorten(t, max_len)
    start = max(0, best_pos - 140)
    end = min(len(t), best_pos + 140)
    return _shorten(t[start:end], max_len)


async def enrich_item_from_detail(source: Source, httpc: HttpClient, item: Item) -> Item:
    """
    Legge la pagina di dettaglio e migliora:
    - summary (snippet “centrato”)
    - published / deadline (se rilevabili)
    """
    if _is_probably_pdf(item.url):
        return item

    html_text = await httpc.get_text(item.url)
    soup = _soup(html_text)

    # published (se c'è un <time datetime=...>)
    pub = None
    time_tag = soup.select_one("time[datetime]")
    if time_tag and time_tag.get("datetime"):
        pub = _try_parse_date(time_tag.get("datetime") or "")

    page_text = _extract_main_text(soup)
    pub2, ddl2 = _extract_published_deadline_from_page_text(page_text)

    published_dt = pub or pub2
    deadline_dt = ddl2

    new_summary = _best_snippet(page_text, 250)

    return _make_item(
        source,
        title=item.title,
        url=item.url,
        published=published_dt or (_try_parse_date(item.published) if item.published else None),
        deadline=deadline_dt or (_try_parse_date(item.deadline) if item.deadline else None),
        summary=new_summary or item.summary,
        external_id=item.external_id,
        meta={**(item.meta or {}), "detail_enriched": True, "detail_fetchable": True},
    )


def parse_generic_links(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    out = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href or not text:
            continue
        if len(text) < 6:
            continue
        url = urljoin(source.url, href)
        ctx = a.parent.get_text(" ", strip=True) if a.parent else text
        out.append((text, url, ctx))

    seen = set()
    uniq = []
    for t, u, c in out:
        cu = canonicalize_url(u)
        if cu in seen:
            continue
        seen.add(cu)
        uniq.append((t, u, c))
    return uniq[:60]


def parse_sicilia_bandi(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    """
    Pagina bandi Sicilia: cerchiamo link “titolo bando” in modo più mirato.
    """
    candidates = []

    # selettori tipici Drupal/views
    selectors = [
        ".view-content .views-row .views-field-title a[href]",
        ".view-content .views-row h3 a[href]",
        ".view-content .views-row a[href]",
    ]
    for sel in selectors:
        for a in soup.select(sel):
            href = (a.get("href") or "").strip()
            text = a.get_text(" ", strip=True)
            if not href or not text or len(text) < 8:
                continue
            url = urljoin(source.url, href)
            # filtra link utili
            path = urlparse(url).path.lower()
            if "bandi" not in path and "avvisi" not in path and "servizi-informativi" not in path and "/node/" not in path:
                continue
            ctx = a.parent.get_text(" ", strip=True) if a.parent else text
            candidates.append((text, url, ctx))

    # fallback: generic
    if not candidates:
        candidates = parse_generic_links(source, soup)

    # dedup
    seen = set()
    uniq = []
    for t, u, c in candidates:
        cu = canonicalize_url(u)
        if cu in seen:
            continue
        seen.add(cu)
        uniq.append((t, u, c))
    return uniq[:60]


def parse_er_bandi(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    candidates = []
    selectors = [
        "main a[href]",
        "article a[href]",
        ".view-content a[href]",
    ]
    for sel in selectors:
        for a in soup.select(sel):
            href = (a.get("href") or "").strip()
            text = a.get_text(" ", strip=True)
            if not href or not text or len(text) < 8:
                continue
            url = urljoin(source.url, href)
            if "bandi.regione.emilia-romagna.it" not in url:
                continue
            candidates.append((text, url, a.parent.get_text(" ", strip=True) if a.parent else text))

    if not candidates:
        candidates = parse_generic_links(source, soup)

    seen = set()
    uniq = []
    for t, u, c in candidates:
        cu = canonicalize_url(u)
        if cu in seen:
            continue
        seen.add(cu)
        uniq.append((t, u, c))
    return uniq[:60]


def parse_invitalia_incentivi(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    out = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not text or not href or len(text) < 6:
            continue
        if "/per-le-imprese/" not in href and "/incentivi" not in href and "/strumenti" not in href:
            continue
        url = urljoin(source.url, href)
        ctx = a.parent.get_text(" ", strip=True) if a.parent else text
        out.append((text, url, ctx))
    return out[:60]


def parse_interreg(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    out = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href or not text or len(text) < 8:
            continue
        if "/calls-for-projects/" not in href and not href.startswith("/calls-for-projects/"):
            continue
        url = urljoin(source.url, href)
        out.append((text, url, a.parent.get_text(" ", strip=True) if a.parent else text))
    return out[:60]


def parse_eic(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    out = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href or not text or len(text) < 8:
            continue
        h = href.lower()
        t = text.lower()
        if "eic" not in h and "eic" not in t and "accelerator" not in h and "challenge" not in h:
            continue
        if "funding-opportunit" not in h and "accelerator" not in h and "challenge" not in h and "call" not in h:
            continue
        url = urljoin(source.url, href)
        out.append((text, url, a.parent.get_text(" ", strip=True) if a.parent else text))
    return out[:60]


def parse_pico_tag(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    out = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href or not text or len(text) < 8:
            continue
        if "/tag/bandi/" in href:
            continue
        url = urljoin(source.url, href)
        if "pico.coop" not in url:
            continue
        out.append((text, url, a.parent.get_text(" ", strip=True) if a.parent else text))
    return out[:60]


def parse_pico_bandi(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    # pagina bandi e avvisi: usa link “articolo”
    return parse_pico_tag(source, soup)


def parse_frd_bandi(source: Source, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    out = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href or not text or len(text) < 6:
            continue
        url = urljoin(source.url, href)
        if "fondorepubblicadigitale.it" not in url:
            continue
        if "/bandi/" not in url and "bando" not in text.lower():
            continue
        out.append((text, url, a.parent.get_text(" ", strip=True) if a.parent else text))
    return out[:60]


_HTML_PARSERS: dict[str, Callable[[Source, BeautifulSoup], list[tuple[str, str, str]]]] = {
    "generic_links": parse_generic_links,
    "invitalia_incentivi": parse_invitalia_incentivi,
    "er_bandi": parse_er_bandi,
    "sicilia_bandi": parse_sicilia_bandi,
    "opencoesione": parse_generic_links,
    "interreg": parse_interreg,
    "eic": parse_eic,
    "pico_bandi": parse_pico_bandi,
    "pico_tag": parse_pico_tag,
    "frd_bandi": parse_frd_bandi,
}


async def fetch_html(source: Source, httpc: HttpClient) -> list[Item]:
    raw = await httpc.get_text(source.url)
    soup = _soup(raw)
    parser_key = source.parser or "generic_links"
    parser = _HTML_PARSERS.get(parser_key, parse_generic_links)
    triples = parser(source, soup)

    items: list[Item] = []
    for title, url, ctx in triples:
        pub = _extract_first_date_like(ctx)
        ddl = _extract_deadline_from_text(ctx)
        summary = ctx if ctx else title
        items.append(
            _make_item(
                source,
                title=title,
                url=url,
                published=pub,
                deadline=ddl,
                summary=summary,
                meta={"detail_fetchable": (not _is_probably_pdf(url))},
            )
        )
    return items


# ---------- GURS PDF (lasciato com'è: continuerà a funzionare, ma può essere lento) ----------
_BANDO_KEYWORDS = [
    "AVVISO PUBBLICO",
    "BANDO",
    "MANIFESTAZIONE DI INTERESSE",
    "CONTRIBUTI",
    "FINANZIAMENTO",
    "R&S",
    "INNOVAZIONE",
    "DIGITALE",
]

_GAZZETTA_DATE_RE = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b")


def _scan_pdf_for_hits(text: str) -> list[tuple[int, str]]:
    up = (text or "").upper()
    hits = []
    for kw in _BANDO_KEYWORDS:
        idx = up.find(kw)
        if idx != -1:
            hits.append((idx, kw))
    hits.sort()
    return hits


def _snippet_around(text: str, idx: int, radius: int = 140) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    start = max(0, idx - radius)
    end = min(len(t), idx + radius)
    return t[start:end]


def _extract_gurs_published(text: str) -> Optional[datetime]:
    m = _GAZZETTA_DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return dtparser.parse(m.group(0), dayfirst=True)
    except Exception:
        return None


def _extract_deadline_from_pdf(text: str) -> Optional[datetime]:
    return _extract_deadline_from_text(text)


def _gurs_candidate_filenames(issue: int, year: int) -> list[str]:
    n2 = f"{issue:02d}"
    n = f"{issue}"
    return [
        f"PI_{n2}_{year}-firm.pdf",
        f"PI_{n2}_{year}-firmata.pdf",
        f"PI_{n2}_{year}.pdf",
        f"PI_{n}_{year}-firm.pdf",
        f"PI_{n}_{year}.pdf",
        f"firm-PI_{n}_{year}.pdf",
        f"firm-PI_{n2}_{year}.pdf",
        f"SO_01_GURS_{issue}_{year}-firmata.pdf",
        f"SO_01_GURS_{issue}_{year}.pdf",
    ]


def _month_dirs(now: datetime) -> list[tuple[int, int]]:
    y, m = now.year, now.month
    if m == 1:
        return [(y, 1), (y - 1, 12)]
    return [(y, m), (y, m - 1)]


async def fetch_gurs_pdf(source: Source, httpc: HttpClient, now: datetime, last_seen_issue: Optional[int]) -> list[Item]:
    year = now.year
    probe = list(range(1, 21)) if last_seen_issue is None else list(range(last_seen_issue + 1, last_seen_issue + 13))
    dirs = _month_dirs(now)

    existing_urls: list[str] = []
    for issue in probe:
        for (yy, mm) in dirs:
            base = f"{source.url}{yy}/{mm:02d}/"
            for fn in _gurs_candidate_filenames(issue, year):
                url = base + fn
                try:
                    ok = await httpc.head_ok(url)
                except Exception:
                    ok = False
                if ok:
                    existing_urls.append(url)

    uniq = []
    seen = set()
    for u in existing_urls:
        cu = canonicalize_url(u)
        if cu in seen:
            continue
        seen.add(cu)
        uniq.append(u)

    items: list[Item] = []
    for pdf_url in uniq:
        try:
            data = await httpc.get_bytes(pdf_url)
        except Exception as e:
            log.warning("GURS download failed %s: %s", pdf_url, e)
            continue

        try:
            reader = PdfReader(io.BytesIO(data))
            parts = []
            for i in range(min(len(reader.pages), 8)):
                parts.append(reader.pages[i].extract_text() or "")
            text = "\n".join(parts)
        except Exception as e:
            log.warning("GURS parse failed %s: %s", pdf_url, e)
            continue

        hits = _scan_pdf_for_hits(text)
        if not hits:
            continue

        published_dt = _extract_gurs_published(text)
        deadline_dt = _extract_deadline_from_pdf(text)

        for i, (idx, kw) in enumerate(hits[:5]):
            snip = _snippet_around(text, idx)
            title = f"GURS: {kw.title()} (estratto)"
            summary = _shorten(snip, 250)
            ext_id = f"{pdf_url}#hit{i}"
            items.append(
                _make_item(
                    source,
                    title=title,
                    url=pdf_url,
                    published=published_dt,
                    deadline=deadline_dt,
                    summary=summary,
                    external_id=ext_id,
                    meta={"detail_fetchable": False},
                )
            )

    return items


async def fetch_items_for_source(
    source: Source,
    httpc: HttpClient,
    now: datetime,
    *,
    gurs_last_seen_issue: Optional[int] = None,
) -> list[Item]:
    if source.kind == "rss":
        return await fetch_rss(source, httpc)
    if source.kind == "html":
        return await fetch_html(source, httpc)
    if source.kind == "gurs_pdf":
        return await fetch_gurs_pdf(source, httpc, now, gurs_last_seen_issue)
    raise ValueError(f"kind non supportato: {source.kind}")
