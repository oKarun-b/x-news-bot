"""Relevance / urgency scoring — §7 freshness bands + §11 geo priorities, transparent & logged."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app import config
from app.logging_setup import get_logger

log = get_logger("x-news-bot.rank")

# ── Geo / topic keyword sets (§1, §11) ───────────────
US_RE = re.compile(r"\b(united states|u\.s\.|us |american|washington|white house|trump|congress|senate|pentagon|federal|republican|democrat|texas|florida|california|new york)\b", re.IGNORECASE)
ME_RE = re.compile(r"\b(iran|iranian|israel|israeli|palestine|palestinian|gaza|middle east|hamas|hezbollah|irgc|tehran|jerusalem|west bank)\b", re.IGNORECASE)
RUUA_RE = re.compile(r"\b(ukraine|ukrainian|russia|russian|kyiv|moscow|zelensky|putin|kremlin|nato)\b", re.IGNORECASE)
UKEU_RE = re.compile(r"\b(uk |britain|british|london|europe|european union|eu |germany|france|italy|spain|poland|paris|berlin)\b", re.IGNORECASE)
TECH_RE = re.compile(r"\b(ai|artificial intelligence|openai|google|microsoft|meta|nvidia|apple|tech|chip|semiconductor|startup|app|software|cyber)\b", re.IGNORECASE)
BIZ_RE = re.compile(r"\b(stock|market|markets|economy|inflation|fed|federal reserve|tariff|trade|wall street|nasdaq|dow|s&p|earnings|billion|merger|ipo|oil price)\b", re.IGNORECASE)
CAMEROON_RE = re.compile(r"\b(cameroon|cameroonian|douala|yaounde|yaoundé|ambazonia|anglophone)\b", re.IGNORECASE)
LOCAL_ONLY_RE = re.compile(r"\b(local|city council|school board|high school|little league|county|village|township)\b", re.IGNORECASE)
AFRICA_LOCAL_RE = re.compile(r"\b(nigeria|kenya|ghana|tanzania|uganda|ethiopia|senegal|mali|niger|chad|congo|zambia|zimbabwe|malawi|angola|mozambique|benin|togo|burkina)\b", re.IGNORECASE)
BREAKING_RE = re.compile(r"\b(breaking|just in|urgent|explodes|shoots|strikes|kills|arrested|resigns|fires|launches|attacks|invasion|ceasefire|summit|verdict|emergency)\b", re.IGNORECASE)

# Public interest: disasters, crime, human stories with mass relevance
INTEREST_RE = re.compile(r"\b(earthquake|hurricane|flood|wildfire|crash|plane|shot|dead|killed|victims|missing|rescued|evacuat)\b", re.IGNORECASE)


def _age_hours(dt: datetime | None, now: datetime) -> float | None:
    if dt is None:
        return None
    try:
        return max(0.0, (now - dt).total_seconds() / 3600)
    except Exception:
        return None


def freshness_score(age_h: float | None, now: datetime, cluster: dict) -> tuple[float, bool, bool]:
    """§7 bands. Returns (score, is_breaking_fresh, is_reject_old)."""
    if age_h is None:
        return 10.0, False, False  # unknown: neutral-mid, never reject on age alone
    if age_h <= 0.5:
        return float(config.RANK_FRESHNESS), True, False      # 0-30 min: EXTREMELY HIGH
    if age_h <= 1.5:
        return config.RANK_FRESHNESS * 0.85, True, False      # 30-90 min: VERY HIGH
    if age_h <= 3:
        return config.RANK_FRESHNESS * 0.65, False, False     # 90m-3h: HIGH
    if age_h <= 6:
        return config.RANK_FRESHNESS * 0.45, False, False     # 3-6h: MEDIUM
    if age_h <= 12:
        return config.RANK_FRESHNESS * 0.25, False, False     # 6-12h: LOW
    if age_h <= 24:
        # very low unless still developing
        developing = cluster.get("development_level", 1) > 1 or cluster.get("source_count", 1) >= 3
        return (config.RANK_FRESHNESS * 0.1 if developing else 0.0), False, False
    return 0.0, False, True  # >24h: reject unless... (caller may override if developing)


def score_cluster(cluster: dict, now: datetime, state_clusters: dict | None = None) -> tuple[float, dict[str, float]]:
    """§11 conceptual scoring. Returns (total, breakdown)."""
    bd: dict[str, float] = {}
    total = 0.0
    arts = cluster.get("articles", [])
    text_blob = " ".join(
        f"{a.get('title','')} {a.get('summary','')}" for a in arts
    ) or cluster.get("representative_title", "")

    # ── Freshness ────────────────────────────────────
    latest = cluster.get("latest_activity") or cluster.get("first_detected")
    age_h = _age_hours(latest, now)
    fresh, breaking_fresh, too_old = freshness_score(age_h, now, cluster)
    bd["freshness"] = round(fresh, 1)
    total += fresh

    # ── Geography / topic boosts (non-exclusive, additive but capped) ──
    geo = 0.0
    if US_RE.search(text_blob):
        geo += config.RANK_US_RELEVANCE
    if ME_RE.search(text_blob):
        geo += config.RANK_MIDDLE_EAST
    if RUUA_RE.search(text_blob):
        geo += config.RANK_GEOPOLITICAL
    if UKEU_RE.search(text_blob):
        geo += config.RANK_UK_EU
    if TECH_RE.search(text_blob):
        geo += config.RANK_TECH
    if BIZ_RE.search(text_blob):
        geo += config.RANK_BUSINESS
    geo = min(geo, 55.0)  # cap stacking (US+ME both relevant is common)
    bd["geo"] = round(geo, 1)
    total += geo

    # ── Developing story ─────────────────────────────
    dev = 0.0
    if cluster.get("development_level", 1) > 1:
        dev += config.RANK_DEVELOPING * 0.6
    if cluster.get("source_count", 1) >= 3:
        dev += config.RANK_DEVELOPING * 0.4
    elif cluster.get("source_count", 1) == 2:
        dev += config.RANK_DEVELOPING * 0.2
    bd["developing"] = round(dev, 1)
    total += dev

    # ── Source quality (tier 1 best → 5 worst) ───────
    tiers = [a.get("tier", 5) for a in arts] or [5]
    best_tier = min(tiers)
    src = {1: 1.0, 2: 0.7, 3: 0.6, 4: 0.5, 5: 0.3}.get(best_tier, 0.3)
    src_score = config.RANK_SOURCE_QUALITY * src
    bd["source_quality"] = round(src_score, 1)
    total += src_score

    # ── Public interest ──────────────────────────────
    interest = 0.0
    if INTEREST_RE.search(text_blob):
        interest += config.RANK_PUBLIC_INTEREST * 0.7
    if BREAKING_RE.search(text_blob):
        interest += config.RANK_PUBLIC_INTEREST * 0.3
    bd["public_interest"] = round(interest, 1)
    total += interest

    # ── Penalties ────────────────────────────────────
    # Old story
    if age_h is not None and age_h > config.NEWS_MAX_AGE_HOURS and not too_old:
        bd["old_penalty"] = float(-config.PENALTY_OLD_STORY)
        total -= config.PENALTY_OLD_STORY
    # >24h outright reject unless developing
    if too_old and cluster.get("development_level", 1) <= 1:
        bd["old_penalty"] = float(-config.PENALTY_OLD_STORY * 2)
        total -= config.PENALTY_OLD_STORY * 2
    # Cameroon-only (§1)
    if CAMEROON_RE.search(text_blob) and not (US_RE.search(text_blob) or ME_RE.search(text_blob) or RUUA_RE.search(text_blob)):
        bd["cameroon_penalty"] = float(-config.PENALTY_CAMEROON)
        total -= config.PENALTY_CAMEROON
    # Local-only / Africa-local without international angle
    elif (LOCAL_ONLY_RE.search(text_blob) or AFRICA_LOCAL_RE.search(text_blob)) and not (
        US_RE.search(text_blob) or ME_RE.search(text_blob) or RUUA_RE.search(text_blob)
        or UKEU_RE.search(text_blob) or TECH_RE.search(text_blob) or BIZ_RE.search(text_blob)
    ):
        bd["local_penalty"] = float(-config.PENALTY_LOCAL_ONLY)
        total -= config.PENALTY_LOCAL_ONLY
    # Low quality source only
    if best_tier >= 4 and not (US_RE.search(text_blob) or ME_RE.search(text_blob) or RUUA_RE.search(text_blob)):
        bd["low_src_penalty"] = float(-config.PENALTY_LOW_QUALITY_SOURCE * 0.5)
        total -= config.PENALTY_LOW_QUALITY_SOURCE * 0.5
    # Already posted recently
    if state_clusters is not None:
        prev = state_clusters.get(cluster["cluster_id"])
        if prev and prev.get("last_posted_at"):
            try:
                last = datetime.fromisoformat(prev["last_posted_at"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                hours_since = (now - last).total_seconds() / 3600
                # Strong penalty within MIN_DEVELOPMENT_GAP, decaying after
                if hours_since < config.MIN_DEVELOPMENT_GAP_MINUTES / 60:
                    penalty = float(config.PENALTY_ALREADY_POSTED)
                else:
                    penalty = 40 * math.exp(-hours_since / 6)
                bd["posted_penalty"] = round(-penalty, 1)
                total -= penalty
            except Exception:
                bd["posted_penalty"] = 0.0

    total = round(max(0.0, total), 1)
    bd["TOTAL"] = total
    return total, bd


def rank_clusters(
    clusters: list[dict],
    now: datetime | None = None,
    state_clusters: dict | None = None,
) -> list[dict]:
    """Score and sort clusters descending. Annotates each with _score/_breakdown and rejects stale."""
    if now is None:
        now = datetime.now(timezone.utc)
    scored = []
    for c in clusters:
        score, bd = score_cluster(c, now, state_clusters)
        c["_score"] = score
        c["_breakdown"] = bd
        scored.append(c)
    scored.sort(key=lambda x: x["_score"], reverse=True)
    for c in scored[:6]:
        log.info(
            "  rank %s score=%.1f %s — %s",
            c["cluster_id"][:8], c["_score"], c["_breakdown"], c["representative_title"][:70],
        )
    return scored
