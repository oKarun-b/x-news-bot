"""
Central configuration. All magic numbers, feed list, and env handling live here.
No secrets are ever logged.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# ── Paths ────────────────────────────────────────────
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"
FIXED_POSTS_PATH = DATA_DIR / "fixed_posts.json"

# ── Helpers ──────────────────────────────────────────

def _env_str(key: str, default: str) -> str:
    v = os.getenv(key)
    return v.strip() if v is not None and v.strip() != "" else default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise ValueError(f"Env var {key} must be an integer, got: {raw!r}")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        raise ValueError(f"Env var {key} must be a float, got: {raw!r}")


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    v = raw.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"Env var {key} must be a boolean, got: {raw!r}")


def _parse_hhmm(value: str, label: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"{label} must be HH:MM, got {value!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"{label} out of range, got {value!r}")
    return h, m


# ── Timezone ─────────────────────────────────────────

def _resolve_timezone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as exc:
        # Fall back to UTC with a clear warning elsewhere; never crash on bad TZ.
        import warnings
        warnings.warn(f"Unknown timezone {name!r} ({exc}); falling back to UTC")
        from zoneinfo import ZoneInfo
        return ZoneInfo("UTC")


BOT_TIMEZONE_NAME: str = _env_str("BOT_TIMEZONE", "UTC")
BOT_TIMEZONE = _resolve_timezone(BOT_TIMEZONE_NAME)

# ── Identity / mode ──────────────────────────────────
BOT_ENABLED: bool = _env_bool("BOT_ENABLED", True)
DRY_RUN: bool = _env_bool("DRY_RUN", False)
LOG_LEVEL: str = _env_str("LOG_LEVEL", "INFO").upper()

# ── Monitoring windows (BOT_TIMEZONE) ────────────────
ACTIVE_MONITORING_START: str = _env_str("ACTIVE_MONITORING_START", "06:00")
ACTIVE_MONITORING_END: str = _env_str("ACTIVE_MONITORING_END", "23:30")
ACTIVE_START_H, ACTIVE_START_M = _parse_hhmm(ACTIVE_MONITORING_START, "ACTIVE_MONITORING_START")
ACTIVE_END_H, ACTIVE_END_M = _parse_hhmm(ACTIVE_MONITORING_END, "ACTIVE_MONITORING_END")
OVERNIGHT_COLLECTION: bool = _env_bool("OVERNIGHT_COLLECTION", True)
ALLOW_BREAKING_OUTSIDE_WINDOW: bool = _env_bool("ALLOW_BREAKING_OUTSIDE_WINDOW", True)

# ── Posting window ───────────────────────────────────
POSTING_START_HOUR: int = _env_int("POSTING_START_HOUR", 6)
POSTING_END_HOUR: int = _env_int("POSTING_END_HOUR", 23)

# ── Daily quota (§3) ─────────────────────────────────
DAILY_POST_TARGET: int = _env_int("DAILY_POST_TARGET", 10)
DAILY_POST_MINIMUM: int = _env_int("DAILY_POST_MINIMUM", 10)
DAILY_POST_HARD_MAX: int = _env_int("DAILY_POST_HARD_MAX", 14)

# ── Limits ───────────────────────────────────────────
MAX_TOTAL_POSTS_PER_DAY: int = _env_int("MAX_TOTAL_POSTS_PER_DAY", DAILY_POST_HARD_MAX)
MAX_AI_POSTS_PER_DAY: int = _env_int("MAX_AI_POSTS_PER_DAY", DAILY_POST_HARD_MAX)
RESERVED_FIXED_POSTS: int = _env_int("RESERVED_FIXED_POSTS", 0)
MAX_BUFFER_AHEAD_POSTS: int = _env_int("MAX_BUFFER_AHEAD_POSTS", 4)
# Rolling scheduling horizon for normal news (§21: 3-6h)
BUFFER_HORIZON_MINUTES: int = _env_int("BUFFER_HORIZON_MINUTES", _env_int("MAX_SCHEDULE_HORIZON_MINUTES", 360))
MAX_SCHEDULE_HORIZON_MINUTES: int = BUFFER_HORIZON_MINUTES
# Per-run post cap (quota engine scales actual posts/run up to this)
MAX_NEW_POSTS_PER_RUN: int = _env_int("MAX_NEW_POSTS_PER_RUN", 4)
MAX_AI_CALLS_PER_RUN: int = _env_int("MAX_AI_CALLS_PER_RUN", 3)
MAX_AI_CALLS_PER_RUN_CATCHUP: int = _env_int("MAX_AI_CALLS_PER_RUN_CATCHUP", 5)
# OpenRouter free tier = 50 requests/day across ALL free models (resets 00:00 UTC).
# Keep this below 50 so rotations + failures never exhaust the daily pool.
MAX_AI_CALLS_PER_DAY: int = _env_int("MAX_AI_CALLS_PER_DAY", 40)
TOP_CANDIDATES_PER_RUN: int = _env_int("TOP_CANDIDATES_PER_RUN", 6)
TOP_CANDIDATES_CATCHUP: int = _env_int("TOP_CANDIDATES_CATCHUP", 8)

# ── Discovery cadence / chaining (§23) ───────────────
DISCOVERY_INTERVAL_MINUTES: int = _env_int("DISCOVERY_INTERVAL_MINUTES", 12)
ENABLE_CHAIN: bool = _env_bool("ENABLE_CHAIN", True)
CHAIN_WAIT_MAX_MINUTES: int = _env_int("CHAIN_WAIT_MAX_MINUTES", 12)

# ── Gaps ─────────────────────────────────────────────
MIN_NORMAL_GAP_MINUTES: int = _env_int("MIN_NORMAL_GAP_MINUTES", 25)
MAX_NORMAL_GAP_MINUTES: int = _env_int("MAX_NORMAL_GAP_MINUTES", 150)
BREAKING_MIN_DELAY_MINUTES: int = _env_int("BREAKING_MIN_DELAY_MINUTES", 2)
BREAKING_MAX_DELAY_MINUTES: int = _env_int("BREAKING_MAX_DELAY_MINUTES", 4)

# ── Breaking / priority / freshness (§7) ─────────────
BREAKING_MAX_AGE_MINUTES: int = _env_int("BREAKING_MAX_AGE_MINUTES", 90)
NEWS_MAX_AGE_HOURS: int = _env_int("NEWS_MAX_AGE_HOURS", 12)
ENABLE_BREAKING_OVERRIDE: bool = _env_bool("ENABLE_BREAKING_OVERRIDE", True)
BREAKING_PRIORITY: int = _env_int("BREAKING_PRIORITY", 100)
URGENT_PRIORITY: int = _env_int("URGENT_PRIORITY", 90)
HIGH_PRIORITY: int = _env_int("HIGH_PRIORITY", 75)
NORMAL_PRIORITY: int = _env_int("NORMAL_PRIORITY", 50)
LOW_PRIORITY: int = _env_int("LOW_PRIORITY", 25)
MIN_DEVELOPMENT_GAP_MINUTES: int = _env_int("MIN_DEVELOPMENT_GAP_MINUTES", 60)

# ── Content / AI ─────────────────────────────────────
MAX_POST_LENGTH: int = _env_int("MAX_POST_LENGTH", 280)
PREFERRED_POST_MIN: int = _env_int("PREFERRED_POST_MIN", 100)
PREFERRED_POST_MAX: int = _env_int("PREFERRED_POST_MAX", 220)
HARD_MAX_POST_LENGTH: int = _env_int("HARD_MAX_POST_LENGTH", 280)
ALLOW_HASHTAGS: bool = _env_bool("ALLOW_HASHTAGS", False)
# ── Images (§images) ─────────────────────────────────
ENABLE_IMAGES: bool = _env_bool("ENABLE_IMAGES", True)
MAX_IMAGES_PER_POST: int = _env_int("MAX_IMAGES_PER_POST", 1)
MAX_IMAGE_POSTS_PER_DAY: int = _env_int("MAX_IMAGE_POSTS_PER_DAY", 4)
# Curated library URLs (public repo raw files — Buffer-valid: public/direct/https/stable)
IMAGE_BASE_URL: str = _env_str(
    "IMAGE_BASE_URL",
    f"https://raw.githubusercontent.com/{_env_str('GITHUB_REPOSITORY', 'oKarun-b/x-news-bot')}/main/data/images",
)
LANGUAGE: str = _env_str("LANGUAGE", "en")
TARGET_MARKETS: list[str] = [s.strip() for s in _env_str("TARGET_MARKETS", "US,UK,EU,GLOBAL").split(",") if s.strip()]
OPENROUTER_MODEL: str = _env_str("OPENROUTER_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free")
OPENROUTER_FALLBACK_MODELS: list[str] = [
    s.strip() for s in _env_str(
        "OPENROUTER_FALLBACK_MODELS",
        "liquid/lfm-2.5-2.6b:free,google/gemma-4-26b-a4b-it:free,nvidia/nemotron-3.5-lightning:free",
    ).split(",") if s.strip()
]
CLUSTER_SIMILARITY_THRESHOLD: float = _env_float("CLUSTER_SIMILARITY_THRESHOLD", 0.45)
STATE_RETENTION_DAYS: int = _env_int("STATE_RETENTION_DAYS", 7)
ARTICLE_RETENTION_DAYS: int = _env_int("ARTICLE_RETENTION_DAYS", 3)
FEED_TIMEOUT_SECONDS: int = _env_int("FEED_TIMEOUT_SECONDS", 10)
ARTICLE_FETCH_MAX: int = _env_int("ARTICLE_FETCH_MAX", 2)
ENABLE_QUEUE_RESHUFFLE: bool = _env_bool("ENABLE_QUEUE_RESHUFFLE", False)

# ── Editorial ranking weights (§11) — configurable ───
RANK_FRESHNESS: int = _env_int("RANK_FRESHNESS", 30)
RANK_US_RELEVANCE: int = _env_int("RANK_US_RELEVANCE", 30)
RANK_GEOPOLITICAL: int = _env_int("RANK_GEOPOLITICAL", 25)
RANK_MIDDLE_EAST: int = _env_int("RANK_MIDDLE_EAST", 25)
RANK_UK_EU: int = _env_int("RANK_UK_EU", 15)
RANK_TECH: int = _env_int("RANK_TECH", 15)
RANK_BUSINESS: int = _env_int("RANK_BUSINESS", 15)
RANK_DEVELOPING: int = _env_int("RANK_DEVELOPING", 20)
RANK_SOURCE_QUALITY: int = _env_int("RANK_SOURCE_QUALITY", 20)
RANK_PUBLIC_INTEREST: int = _env_int("RANK_PUBLIC_INTEREST", 15)
PENALTY_OLD_STORY: int = _env_int("PENALTY_OLD_STORY", 30)
PENALTY_LOCAL_ONLY: int = _env_int("PENALTY_LOCAL_ONLY", 30)
PENALTY_CAMEROON: int = _env_int("PENALTY_CAMEROON", 40)
PENALTY_LOW_QUALITY_SOURCE: int = _env_int("PENALTY_LOW_QUALITY_SOURCE", 25)
PENALTY_DUPLICATE: int = _env_int("PENALTY_DUPLICATE", 100)
PENALTY_ALREADY_POSTED: int = _env_int("PENALTY_ALREADY_POSTED", 100)
PENALTY_LOW_NEWS_VALUE: int = _env_int("PENALTY_LOW_NEWS_VALUE", 30)
# Selection floor scales with quota pressure; never select below this
MIN_CANDIDATE_SCORE: int = _env_int("MIN_CANDIDATE_SCORE", 18)
MIN_CANDIDATE_SCORE_CATCHUP: int = _env_int("MIN_CANDIDATE_SCORE_CATCHUP", 12)

# ── Buffer ───────────────────────────────────────────
BUFFER_API_URL: str = _env_str("BUFFER_API_URL", "https://api.buffer.com")
BUFFER_ACCESS_TOKEN: str = _env_str("BUFFER_ACCESS_TOKEN", "")
BUFFER_CHANNEL_ID: str = _env_str("BUFFER_CHANNEL_ID", "")
BUFFER_ORGANIZATION_ID: str = _env_str("BUFFER_ORGANIZATION_ID", "")

# ── OpenRouter ───────────────────────────────────────
OPENROUTER_API_KEY: str = _env_str("OPENROUTER_API_KEY", "")

# ── Effective dry-run ────────────────────────────────
# BOT_ENABLED=false implies dry-run semantics (pipeline runs, no Buffer sends).
EFFECTIVE_DRY_RUN: bool = DRY_RUN or (not BOT_ENABLED)

# ── Invariants ───────────────────────────────────────
if MAX_AI_POSTS_PER_DAY + RESERVED_FIXED_POSTS > MAX_TOTAL_POSTS_PER_DAY:
    raise ValueError(
        f"MAX_AI_POSTS_PER_DAY ({MAX_AI_POSTS_PER_DAY}) + "
        f"RESERVED_FIXED_POSTS ({RESERVED_FIXED_POSTS}) "
        f"must not exceed MAX_TOTAL_POSTS_PER_DAY ({MAX_TOTAL_POSTS_PER_DAY})"
    )
if MIN_NORMAL_GAP_MINUTES > MAX_NORMAL_GAP_MINUTES:
    raise ValueError("MIN_NORMAL_GAP_MINUTES must be <= MAX_NORMAL_GAP_MINUTES")
if MAX_POST_LENGTH > HARD_MAX_POST_LENGTH:
    raise ValueError("MAX_POST_LENGTH must be <= HARD_MAX_POST_LENGTH")


# ── Feed registry ────────────────────────────────────
# Tiers (§9): tier = source quality AND topic priority
#   1 = PRIMARY (US / Middle East / Russia-Ukraine)
#   2 = INTERNATIONAL (UK / Europe / world affairs)
#   3 = TECHNOLOGY
#   4 = BUSINESS / MARKETS
#   5 = OTHER (broad fallbacks)
@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    category: str
    tier: int
    enabled: bool = True


_GN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

FEEDS: list[Feed] = [
    # ═══ TIER 1 — PRIMARY: US ═══
    Feed("GN — US breaking news", _GN.format(q="US+breaking+news+when:2h"), "us", 1),
    Feed("GN — US politics", _GN.format(q="US+politics+when:4h"), "us", 1),
    Feed("GN — Donald Trump", _GN.format(q="Trump+when:4h"), "us", 1),
    Feed("GN — White House", _GN.format(q="White+House+when:6h"), "us", 1),
    Feed("GN — US government", _GN.format(q="US+government+Congress+when:6h"), "us", 1),
    Feed("GN — US foreign policy", _GN.format(q="US+foreign+policy+when:6h"), "us", 1),
    # ═══ TIER 1 — PRIMARY: Middle East ═══
    Feed("GN — Iran", _GN.format(q="Iran+when:4h"), "middle_east", 1),
    Feed("GN — Israel", _GN.format(q="Israel+when:4h"), "middle_east", 1),
    Feed("GN — Palestine Gaza", _GN.format(q="Palestine+OR+Gaza+when:4h"), "middle_east", 1),
    Feed("GN — Middle East", _GN.format(q="Middle+East+when:6h"), "middle_east", 1),
    # ═══ TIER 1 — PRIMARY: Russia / Ukraine ═══
    Feed("GN — Ukraine", _GN.format(q="Ukraine+when:4h"), "russia_ukraine", 1),
    Feed("GN — Russia Ukraine war", _GN.format(q="Russia+Ukraine+when:4h"), "russia_ukraine", 1),
    Feed("GN — US Russia", _GN.format(q="US+Russia+talks+when:6h"), "russia_ukraine", 1),
    # ═══ TIER 2 — INTERNATIONAL ═══
    Feed("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "world", 2),
    Feed("BBC US & Canada", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", "us", 2),
    Feed("GN — UK politics", _GN.format(q="UK+politics+when:6h"), "uk_europe", 2),
    Feed("GN — Europe", _GN.format(q="Europe+news+when:6h"), "uk_europe", 2),
    Feed("GN — world news", _GN.format(q="world+news+when:4h"), "world", 2),
    # ═══ TIER 3 — TECHNOLOGY ═══
    Feed("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "technology", 3),
    Feed("GN — artificial intelligence", _GN.format(q="artificial+intelligence+when:6h"), "technology", 3),
    Feed("GN — OpenAI", _GN.format(q="OpenAI+when:12h"), "technology", 3),
    Feed("GN — NVIDIA", _GN.format(q="NVIDIA+when:12h"), "technology", 3),
    Feed("GN — tech major", _GN.format(q="Google+OR+Microsoft+OR+Meta+OR+Apple+announcement+when:6h"), "technology", 3),
    # ═══ TIER 4 — BUSINESS / MARKETS ═══
    Feed("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "business", 4),
    Feed("GN — stock market", _GN.format(q="stock+market+when:6h"), "business", 4),
    Feed("GN — Federal Reserve", _GN.format(q="Federal+Reserve+when:12h"), "business", 4),
    Feed("GN — markets economy", _GN.format(q="markets+economy+when:6h"), "business", 4),
    # ═══ TIER 5 — OTHER (major international significance only) ═══
    Feed("BBC News", "https://feeds.bbci.co.uk/news/rss.xml", "general", 5),
    Feed("GN — Elon Musk", _GN.format(q="Elon+Musk+when:8h"), "technology", 5),
]
