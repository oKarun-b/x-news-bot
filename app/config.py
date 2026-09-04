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
POSTING_START_HOUR: int = _env_int("POSTING_START_HOUR", 7)
POSTING_END_HOUR: int = _env_int("POSTING_END_HOUR", 22)

# ── Limits ───────────────────────────────────────────
MAX_TOTAL_POSTS_PER_DAY: int = _env_int("MAX_TOTAL_POSTS_PER_DAY", 10)
MAX_AI_POSTS_PER_DAY: int = _env_int("MAX_AI_POSTS_PER_DAY", 8)
RESERVED_FIXED_POSTS: int = _env_int("RESERVED_FIXED_POSTS", 2)
MAX_BUFFER_AHEAD_POSTS: int = _env_int("MAX_BUFFER_AHEAD_POSTS", 3)
MAX_SCHEDULE_HORIZON_MINUTES: int = _env_int("MAX_SCHEDULE_HORIZON_MINUTES", 180)
MAX_NEW_POSTS_PER_RUN: int = _env_int("MAX_NEW_POSTS_PER_RUN", 2)
MAX_AI_CALLS_PER_RUN: int = _env_int("MAX_AI_CALLS_PER_RUN", 2)
TOP_CANDIDATES_PER_RUN: int = _env_int("TOP_CANDIDATES_PER_RUN", 6)

# ── Gaps ─────────────────────────────────────────────
MIN_NORMAL_GAP_MINUTES: int = _env_int("MIN_NORMAL_GAP_MINUTES", 25)
MAX_NORMAL_GAP_MINUTES: int = _env_int("MAX_NORMAL_GAP_MINUTES", 180)
BREAKING_MIN_DELAY_MINUTES: int = _env_int("BREAKING_MIN_DELAY_MINUTES", 2)

# ── Breaking / priority ──────────────────────────────
BREAKING_MAX_AGE_MINUTES: int = _env_int("BREAKING_MAX_AGE_MINUTES", 180)
BREAKING_PRIORITY: int = _env_int("BREAKING_PRIORITY", 100)
URGENT_PRIORITY: int = _env_int("URGENT_PRIORITY", 90)
HIGH_PRIORITY: int = _env_int("HIGH_PRIORITY", 75)
NORMAL_PRIORITY: int = _env_int("NORMAL_PRIORITY", 50)
LOW_PRIORITY: int = _env_int("LOW_PRIORITY", 25)
MIN_DEVELOPMENT_GAP_MINUTES: int = _env_int("MIN_DEVELOPMENT_GAP_MINUTES", 90)

# ── Content / AI ─────────────────────────────────────
MAX_POST_LENGTH: int = _env_int("MAX_POST_LENGTH", 260)
HARD_MAX_POST_LENGTH: int = _env_int("HARD_MAX_POST_LENGTH", 280)
OPENROUTER_MODEL: str = _env_str("OPENROUTER_MODEL", "liquid/lfm-2.5-2.6b:free")
CLUSTER_SIMILARITY_THRESHOLD: float = _env_float("CLUSTER_SIMILARITY_THRESHOLD", 0.45)
STATE_RETENTION_DAYS: int = _env_int("STATE_RETENTION_DAYS", 7)
FEED_TIMEOUT_SECONDS: int = _env_int("FEED_TIMEOUT_SECONDS", 15)
ARTICLE_FETCH_MAX: int = _env_int("ARTICLE_FETCH_MAX", 3)
ENABLE_QUEUE_RESHUFFLE: bool = _env_bool("ENABLE_QUEUE_RESHUFFLE", False)

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
@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    category: str
    tier: int  # 1 = most reputable, 3 = broad search
    enabled: bool = True


FEEDS: list[Feed] = [
    # GENERAL / WORLD
    Feed("BBC News", "https://feeds.bbci.co.uk/news/rss.xml", "general", 1),
    Feed("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "world", 1),
    Feed("BBC US & Canada", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", "world", 1),
    Feed("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "business", 1),
    Feed("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "technology", 1),
    Feed("BBC Science & Environment", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "science", 1),
    # GOOGLE NEWS — trending
    Feed("Google News — Top", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "general", 2),
    # POLITICS
    Feed("Google News — Donald Trump", "https://news.google.com/rss/search?q=Donald+Trump&hl=en-US&gl=US&ceid=US:en", "politics", 2),
    Feed("Google News — Trump legal", "https://news.google.com/rss/search?q=Trump+legal&hl=en-US&gl=US&ceid=US:en", "politics", 2),
    Feed("Google News — US politics", "https://news.google.com/rss/search?q=US+politics&hl=en-US&gl=US&ceid=US:en", "politics", 2),
    Feed("Google News — White House", "https://news.google.com/rss/search?q=White+House&hl=en-US&gl=US&ceid=US:en", "politics", 2),
    # WORLD
    Feed("Google News — world news", "https://news.google.com/rss/search?q=world+news&hl=en-US&gl=US&ceid=US:en", "world", 2),
    Feed("Google News — Ukraine Russia", "https://news.google.com/rss/search?q=Ukraine+Russia&hl=en-US&gl=US&ceid=US:en", "world", 2),
    Feed("Google News — Middle East", "https://news.google.com/rss/search?q=Middle+East&hl=en-US&gl=US&ceid=US:en", "world", 2),
    # TECHNOLOGY / AI
    Feed("Google News — AI", "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en", "technology", 2),
    Feed("Google News — AI technology", "https://news.google.com/rss/search?q=AI+technology&hl=en-US&gl=US&ceid=US:en", "technology", 2),
    Feed("Google News — NVIDIA", "https://news.google.com/rss/search?q=NVIDIA&hl=en-US&gl=US&ceid=US:en", "technology", 3),
    Feed("Google News — OpenAI", "https://news.google.com/rss/search?q=OpenAI&hl=en-US&gl=US&ceid=US:en", "technology", 3),
    Feed("Google News — Google AI", "https://news.google.com/rss/search?q=Google+AI&hl=en-US&gl=US&ceid=US:en", "technology", 3),
    # BUSINESS
    Feed("Google News — stock market", "https://news.google.com/rss/search?q=stock+market&hl=en-US&gl=US&ceid=US:en", "business", 2),
    Feed("Google News — business news", "https://news.google.com/rss/search?q=business+news&hl=en-US&gl=US&ceid=US:en", "business", 2),
    Feed("Google News — Elon Musk", "https://news.google.com/rss/search?q=Elon+Musk&hl=en-US&gl=US&ceid=US:en", "business", 3),
]
