"""Relevance / urgency scoring — transparent, additive, logged."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import config
from app.logging_setup import get_logger

log = get_logger("x-news-bot.rank")

# Weights — kept here (not scattered), easy to tune.
WEIGHTS = {
    "recency": 25,          # up to 25 pts (fresh) decaying to 0 over 24h
    "source_count": 20,     # up to 20 pts (distinct sources)
    "source_tier": 15,      # up to 15 pts (reputable tiers)
    "size": 10,             # cluster size bonus up to 10
    "category_boost": 8,    # modest boost for world/politics/tech (general already neutral)
    "freshness_breaking": 10,  # bonus if < BREAKING_MAX_AGE
}

CATEGORY_BOOSTS = {
    "world": 5,
    "politics": 4,
    "technology": 3,
    "business": 2,
    "general": 0,
    "science": 1,
}

# Penalty if cluster was posted recently (hours since last post)
POSTED_PENALTY_HALF_LIFE_H = 12  # penalty halves every 12h since last post


def _age_hours(published_at: datetime | None, now: datetime) -> float | None:
    if published_at is None:
        return None
    try:
        delta = (now - published_at).total_seconds() / 3600
        return max(0.0, delta)
    except Exception:
        return None


def score_cluster(
    cluster: dict,
    now: datetime,
    state_clusters: dict | None = None,
) -> tuple[float, dict[str, float]]:
    """Return (total_score, breakdown)."""
    bd: dict[str, float] = {}
    total = 0.0

    # Recency — based on latest_activity or first_detected
    latest = cluster.get("latest_activity") or cluster.get("first_detected")
    age = _age_hours(latest, now)
    if age is None:
        recency = 8  # unknown age: neutral, not stale nor fresh
    elif age <= 1:
        recency = WEIGHTS["recency"]
    elif age <= 6:
        recency = WEIGHTS["recency"] * (1 - (age - 1) / 10)  # 25→12.5 at 6h
    elif age <= 24:
        recency = max(0, WEIGHTS["recency"] * (1 - age / 24))
    else:
        recency = 0
    bd["recency"] = round(recency, 1)
    total += recency

    # Source count (distinct sources)
    sc = cluster.get("source_count", 1)
    sc_score = min(WEIGHTS["source_count"], (sc - 1) * 8 + (6 if sc >= 3 else 0))
    # 1 source=0, 2=8, 3=22 capped 20 → 2→8, 3→20, 4+→20
    sc_score = min(WEIGHTS["source_count"], sc_score)
    bd["source_count"] = round(sc_score, 1)
    total += sc_score

    # Source tier: lower tier number = more reputable
    # tier_sum is sum of distinct tiers present; fewer distinct + lower numbers = better
    arts = cluster.get("articles", [])
    tiers = [a.get("tier", 3) for a in arts]
    # weight: tier1 presence is good
    has_t1 = any(t == 1 for t in tiers)
    has_t2 = any(t == 2 for t in tiers)
    tier_score = 0
    if has_t1:
        tier_score += 10
    if has_t2:
        tier_score += 5
    # smaller distinct tier sum bonus
    tier_score = min(WEIGHTS["source_tier"], tier_score)
    bd["source_tier"] = round(tier_score, 1)
    total += tier_score

    # Size bonus
    size = cluster.get("size", 1)
    size_score = min(WEIGHTS["size"], (size - 1) * 4)
    bd["size"] = round(size_score, 1)
    total += size_score

    # Category
    cats = [a.get("category", "general") for a in arts]
    # most common category boost
    from collections import Counter
    top_cat = Counter(cats).most_common(1)[0][0] if cats else "general"
    cat_score = CATEGORY_BOOSTS.get(top_cat, 0)
    bd["category"] = round(float(cat_score), 1)
    total += cat_score

    # Breaking freshness bonus
    if age is not None and age * 60 <= config.BREAKING_MAX_AGE_MINUTES:
        bd["breaking_fresh"] = float(WEIGHTS["freshness_breaking"])
        total += WEIGHTS["freshness_breaking"]
    else:
        bd["breaking_fresh"] = 0.0

    # Penalty for recently posted same cluster
    if state_clusters is not None:
        prev = state_clusters.get(cluster["cluster_id"])
        if prev and prev.get("last_posted_at"):
            try:
                last = datetime.fromisoformat(prev["last_posted_at"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                hours_since = (now - last).total_seconds() / 3600
                # exponential decay penalty
                import math
                penalty = 30 * math.exp(-hours_since / POSTED_PENALTY_HALF_LIFE_H) if hours_since >= 0 else 30
                # cap penalty at 30, decays
                penalty = min(30, max(0, penalty))
                bd["posted_penalty"] = round(-penalty, 1)
                total -= penalty
            except Exception:
                bd["posted_penalty"] = 0.0
        else:
            bd["posted_penalty"] = 0.0
    else:
        bd["posted_penalty"] = 0.0

    total = round(max(0, total), 1)
    return total, bd


def rank_clusters(
    clusters: list[dict],
    now: datetime | None = None,
    state_clusters: dict | None = None,
) -> list[dict]:
    """Score and sort clusters descending. Annotates each cluster with _score and _breakdown."""
    if now is None:
        now = datetime.now(timezone.utc)
    scored = []
    for c in clusters:
        score, bd = score_cluster(c, now, state_clusters)
        c["_score"] = score
        c["_breakdown"] = bd
        scored.append(c)
    scored.sort(key=lambda x: x["_score"], reverse=True)
    for c in scored[:8]:
        log.info(
            "  rank %s score=%.1f %s — %s",
            c["cluster_id"][:8], c["_score"], c["_breakdown"], c["representative_title"][:70],
        )
    return scored
