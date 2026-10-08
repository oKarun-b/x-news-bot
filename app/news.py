"""RSS collection — fetches, parses, normalizes. One broken feed never kills the run."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import feedparser
import requests

from app import config
from app.logging_setup import get_logger
from app.normalize import article_id, canonical_url, parse_published, strip_html

log = get_logger("x-news-bot.news")

# Standard structure produced for every article
# {id, title, link, canonical_url, source, published, published_at, summary, category, tier}

USER_AGENT = "x-news-bot/1.0 (+https://github.com/x-news-bot)"


def _extract_media_urls(e) -> list[str]:
    """Pull image URLs from RSS media tags (media:content, media:thumbnail, enclosures)."""
    urls: list[str] = []
    img_exts = (".jpg", ".jpeg", ".png", ".webp")
    # media:content / media:thumbnail (feedparser normalizes to media_content/media_thumbnail)
    for key in ("media_content", "media_thumbnail"):
        for m in (e.get(key) or []):
            u = (m.get("url") or "").strip()
            t = (m.get("type") or "").lower()
            if u.startswith("https://") and (t.startswith("image") or u.lower().endswith(img_exts)):
                urls.append(u)
    # enclosures
    for enc in (e.get("enclosures") or []):
        u = (enc.get("href") or enc.get("url") or "").strip()
        t = (enc.get("type") or "").lower()
        if u.startswith("https://") and (t.startswith("image") or u.lower().endswith(img_exts)):
            urls.append(u)
    # dedupe preserve order, cap 2
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:2]


def _fetch_one(feed, cache_entry: dict | None = None) -> tuple[list[dict], str | None, dict]:
    """Fetch and parse one feed; return articles, error, and refreshed validators."""
    url = feed.url
    name = feed.name
    try:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
        cached = cache_entry or {}
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]
        resp = requests.get(
            url,
            headers=headers,
            timeout=config.FEED_TIMEOUT_SECONDS,
        )
        if resp.status_code == 304:
            return [], None, cached
        resp.raise_for_status()
        content = resp.content
    except requests.RequestException as exc:
        return [], f"{name}: {exc}", cache_entry or {}

    response_headers = getattr(resp, "headers", {})
    refreshed = {}
    for key, header_name in (("etag", "ETag"), ("last_modified", "Last-Modified")):
        value = response_headers.get(header_name) if hasattr(response_headers, "get") else None
        if isinstance(value, str) and value:
            refreshed[key] = value

    try:
        parsed = feedparser.parse(content)
    except Exception as exc:
        return [], f"{name}: parse error: {exc}", refreshed

    # feedparser sets bozo on malformed feeds but still returns entries
    if getattr(parsed, "bozo", False):
        exc = getattr(parsed, "bozo_exception", None)
        if exc:
            log.warning("Feed %s bozo: %s", name, exc)

    entries = getattr(parsed, "entries", []) or []
    articles: list[dict] = []
    for e in entries:
        try:
            title = (getattr(e, "title", None) or e.get("title", "") or "").strip()
            if not title:
                continue
            link = (getattr(e, "link", None) or e.get("link", "") or "").strip()
            if not link:
                continue
            guid = getattr(e, "id", None) or e.get("id") or e.get("guid") or None
            # feedparser exposes summary/description in several keys
            raw_summary = (
                getattr(e, "summary", None)
                or e.get("summary")
                or e.get("description")
                or ""
            )
            summary = strip_html(raw_summary)[:600]

            # published
            pub_raw = (
                getattr(e, "published", None)
                or e.get("published")
                or e.get("pubDate")
                or e.get("updated")
                or ""
            )
            published_at = parse_published(pub_raw) if pub_raw else None
            published_iso = published_at.isoformat() if published_at else ""

            canon = canonical_url(link)
            aid = article_id(guid, link, title)
            source = getattr(parsed.feed, "title", None) or parsed.feed.get("title", "") or name  # type: ignore[attr-defined]
            # feedparser feed.title may be missing
            if not source:
                source = name

            articles.append({
                "id": aid,
                "title": title,
                "link": link,
                "canonical_url": canon,
                "source": str(source).strip(),
                "published": published_iso,
                "published_at": published_at,  # datetime or None (for ranking)
                "summary": summary,
                "category": feed.category,
                "tier": feed.tier,
                "feed_name": name,
                "guid": guid,
                "image_urls": _extract_media_urls(e) if config.ENABLE_IMAGES and config.ENABLE_ARTICLE_IMAGES else [],
            })
        except Exception as exc:
            log.warning("Skipping entry in %s: %s", name, exc)
            continue

    return articles, None, refreshed


def collect_articles(feeds: list | None = None, feed_cache: dict | None = None) -> tuple[list[dict], dict]:
    """
    Fetch all enabled feeds. Returns (all_articles, stats).
    stats: {attempted, succeeded, failed, failed_details, total_articles}
    """
    target = feeds if feeds is not None else [f for f in config.FEEDS if f.enabled]
    all_articles: list[dict] = []
    failed: list[str] = []
    succeeded = 0

    # RSS polling is I/O-bound. A bounded pool avoids spending one timeout per
    # feed while preserving the configured feed order in the final merge.
    workers = max(1, min(config.FEED_FETCH_WORKERS, len(target) or 1))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rss") as pool:
        results = list(pool.map(
            lambda feed: _fetch_one(feed, (feed_cache or {}).get(feed.name)), target
        ))
    not_modified = 0
    for feed, (articles, err, refreshed) in zip(target, results):
        if err:
            failed.append(err)
            log.warning("Feed failed: %s", err)
        else:
            succeeded += 1
            if not articles and refreshed == ((feed_cache or {}).get(feed.name) or {}):
                not_modified += 1
            if feed_cache is not None:
                feed_cache[feed.name] = refreshed
        all_articles.extend(articles)

    stats = {
        "attempted": len(target),
        "succeeded": succeeded,
        "failed": len(failed),
        "failed_details": failed,
        "total_articles": len(all_articles),
        "not_modified": not_modified,
    }
    log.info(
        "Collected %d articles from %d/%d feeds (%d failed)",
        len(all_articles), succeeded, len(target), len(failed),
    )
    if failed:
        for d in failed:
            log.warning("  failed: %s", d)
    return all_articles, stats
