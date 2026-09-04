"""Dedup L1 (URL) + L2 (title). History-aware helpers included."""
from __future__ import annotations

from app.logging_setup import get_logger
from app.normalize import normalize_title, title_hash

log = get_logger("x-news-bot.filters")


def dedupe_by_url(articles: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    out: list[dict] = []
    dups = 0
    for a in articles:
        key = a.get("canonical_url") or a.get("link", "")
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        out.append(a)
    if dups:
        log.info("URL dedupe: removed %d duplicates (%d unique)", dups, len(out))
    return out, dups


def dedupe_by_title(articles: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    out: list[dict] = []
    dups = 0
    for a in articles:
        h = title_hash(normalize_title(a.get("title", "")))
        if h in seen:
            dups += 1
            continue
        seen.add(h)
        out.append(a)
    if dups:
        log.info("Title dedupe: removed %d duplicates (%d unique)", dups, len(out))
    return out, dups


def dedupe_against_history(
    articles: list[dict],
    seen_canonical: set[str] | None = None,
    seen_title_hashes: set[str] | None = None,
) -> tuple[list[dict], int]:
    """Filter articles already seen (from persisted state)."""
    seen_canonical = seen_canonical or set()
    seen_title_hashes = seen_title_hashes or set()
    out: list[dict] = []
    dups = 0
    for a in articles:
        canon = a.get("canonical_url") or a.get("link", "")
        th = title_hash(normalize_title(a.get("title", "")))
        if canon in seen_canonical or th in seen_title_hashes:
            dups += 1
            continue
        out.append(a)
    if dups:
        log.info("History dedupe: removed %d already-seen articles", dups)
    return out, dups
