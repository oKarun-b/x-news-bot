"""RSS collection — fetches, parses, normalizes. One broken feed never kills the run."""
from __future__ import annotations

import time
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


def _fetch_one(feed) -> tuple[list[dict], str | None]:
    """Fetch and parse a single feed. Returns (articles, error). Never raises."""
    url = feed.url
    name = feed.name
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            timeout=config.FEED_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        content = resp.content
    except requests.RequestException as exc:
        return [], f"{name}: {exc}"

    try:
        parsed = feedparser.parse(content)
    except Exception as exc:
        return [], f"{name}: parse error: {exc}"

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
            })
        except Exception as exc:
            log.warning("Skipping entry in %s: %s", name, exc)
            continue

    return articles, None


def collect_articles(feeds: list | None = None) -> tuple[list[dict], dict]:
    """
    Fetch all enabled feeds. Returns (all_articles, stats).
    stats: {attempted, succeeded, failed, failed_details, total_articles}
    """
    target = feeds if feeds is not None else [f for f in config.FEEDS if f.enabled]
    all_articles: list[dict] = []
    failed: list[str] = []
    succeeded = 0

    for feed in target:
        articles, err = _fetch_one(feed)
        if err:
            failed.append(err)
            log.warning("Feed failed: %s", err)
        else:
            succeeded += 1
        all_articles.extend(articles)
        # Tiny politeness delay between feeds (avoid hammering)
        time.sleep(0.15)

    stats = {
        "attempted": len(target),
        "succeeded": succeeded,
        "failed": len(failed),
        "failed_details": failed,
        "total_articles": len(all_articles),
    }
    log.info(
        "Collected %d articles from %d/%d feeds (%d failed)",
        len(all_articles), succeeded, len(target), len(failed),
    )
    if failed:
        for d in failed:
            log.warning("  failed: %s", d)
    return all_articles, stats
