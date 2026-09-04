"""Story clustering — deterministic Jaccard + union-find. No AI."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from app import config
from app.logging_setup import get_logger
from app.normalize import normalize_title

log = get_logger("x-news-bot.clustering")

STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "by", "is", "are", "was", "were", "be", "been", "has", "have", "had", "will",
    "as", "from", "that", "this", "it", "its", "over", "under", "about", "into",
    "after", "before", "says", "says", "say", "new",
})

TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(normalized_title: str) -> set[str]:
    toks = set(TOKEN_RE.findall(normalized_title.lower()))
    return {t for t in toks if t not in STOPWORDS and len(t) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _within_window(a: dict, b: dict, window_hours: int = 48) -> bool:
    """Only cluster stories whose published dates are reasonably close. Missing dates → allow."""
    pa, pb = a.get("published_at"), b.get("published_at")
    if pa is None or pb is None:
        return True
    try:
        delta = abs((pa - pb).total_seconds()) / 3600
        return delta <= window_hours
    except Exception:
        return True


def cluster_articles(
    articles: list[dict],
    threshold: float | None = None,
    window_hours: int = 48,
) -> list[dict]:
    """
    Cluster articles covering the same event.
    Returns list of clusters: {cluster_id, members: [idx], articles: [article], representative_title, sources, source_count, tier_sum, first_detected, latest_activity}
    """
    if not articles:
        return []
    th = threshold if threshold is not None else config.CLUSTER_SIMILARITY_THRESHOLD
    n = len(articles)
    norms = [normalize_title(a.get("title", "")) for a in articles]
    token_sets = [_tokens(nt) for nt in norms]

    uf = _UF(n)
    for i in range(n):
        for j in range(i + 1, n):
            if not _within_window(articles[i], articles[j], window_hours):
                continue
            sim = jaccard(token_sets[i], token_sets[j])
            # Boost: shared numbers / capitalized entities add 0.1
            # Cheap heuristic: if both titles contain same number, bump.
            nums_i = set(re.findall(r"\d+", articles[i].get("title", "")))
            nums_j = set(re.findall(r"\d+", articles[j].get("title", "")))
            if nums_i and nums_i & nums_j:
                sim = min(1.0, sim + 0.10)
            if sim >= th:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        r = uf.find(idx)
        groups.setdefault(r, []).append(idx)

    clusters: list[dict] = []
    for members in groups.values():
        # Deterministic cluster_id from sorted member article IDs
        member_ids = sorted(articles[i]["id"] for i in members)
        raw = "|".join(member_ids)
        cid = hashlib.sha256(raw.encode()).hexdigest()[:12]
        arts = [articles[i] for i in members]
        # Representative: longest title (most informative) or most recent
        rep = max(arts, key=lambda a: len(a.get("title", "")))
        sources = sorted({a.get("source", "") for a in arts})
        # Tier sum (lower tier number = more reputable, so weight inversely)
        tier_sum = sum({a.get("tier", 3) for a in arts})
        # Published window
        dates = [a.get("published_at") for a in arts if a.get("published_at")]
        first = min(dates) if dates else None
        latest = max(dates) if dates else None
        clusters.append({
            "cluster_id": cid,
            "members": members,
            "articles": arts,
            "member_ids": member_ids,
            "representative_title": rep.get("title", ""),
            "representative_article": rep,
            "sources": sources,
            "source_count": len(sources),
            "tier_sum": tier_sum,
            "first_detected": first,
            "latest_activity": latest,
            "size": len(members),
        })

    # Sort largest / most-sourced first for logging convenience (ranking will re-sort by score)
    clusters.sort(key=lambda c: (c["source_count"], c["size"]), reverse=True)
    log.info("Clustering: %d articles → %d clusters (threshold=%.2f)", n, len(clusters), th)
    for c in clusters[:5]:
        log.info("  cluster %s: %d articles, %d sources — %s", c["cluster_id"][:8], c["size"], c["source_count"], c["representative_title"][:80])
    return clusters
