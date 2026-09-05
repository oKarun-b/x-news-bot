"""
Verified X account registry — the ONLY source of @handles the AI may use.

Never invent a handle. If an entity isn't here, write its plain name.
Keep this file as the single place to add/update verified handles.

Structure:
  "Display Name": {"handle": "@Handle", "type": "source|person|entity", "priority": 1}

priority 1 = high (major outlets / primary figures), 2 = secondary
"""
from __future__ import annotations

import re

# ── Registry ─────────────────────────────────────────
REGISTRY: dict[str, dict] = {
    # Sources — major outlets
    "BBC News": {"handle": "@BBCNews", "type": "source", "priority": 1},
    "BBC World": {"handle": "@BBCWorld", "type": "source", "priority": 1},
    "BBC Breaking": {"handle": "@BBCBreaking", "type": "source", "priority": 1},
    "BBC": {"handle": "@BBCNews", "type": "source", "priority": 1},
    "Reuters": {"handle": "@Reuters", "type": "source", "priority": 1},
    "AFP": {"handle": "@AFP", "type": "source", "priority": 1},
    "Associated Press": {"handle": "@AP", "type": "source", "priority": 1},
    "AP": {"handle": "@AP", "type": "source", "priority": 1},
    "The Guardian": {"handle": "@guardian", "type": "source", "priority": 1},
    "Guardian": {"handle": "@guardian", "type": "source", "priority": 1},
    "CNN": {"handle": "@CNN", "type": "source", "priority": 1},
    "New York Times": {"handle": "@nytimes", "type": "source", "priority": 1},
    "NYT": {"handle": "@nytimes", "type": "source", "priority": 1},
    "Al Jazeera": {"handle": "@AJEnglish", "type": "source", "priority": 1},
    "Bloomberg": {"handle": "@business", "type": "source", "priority": 1},
    "Financial Times": {"handle": "@FT", "type": "source", "priority": 1},
    "Wall Street Journal": {"handle": "@WSJ", "type": "source", "priority": 1},
    "WSJ": {"handle": "@WSJ", "type": "source", "priority": 1},

    # Persons / entities — verified handles
    "Elon Musk": {"handle": "@elonmusk", "type": "person", "priority": 1},
    "Donald Trump": {"handle": "@realDonaldTrump", "type": "person", "priority": 1},
    "Trump": {"handle": "@realDonaldTrump", "type": "person", "priority": 1},
    "White House": {"handle": "@WhiteHouse", "type": "entity", "priority": 1},
    "OpenAI": {"handle": "@OpenAI", "type": "entity", "priority": 1},
    "Google": {"handle": "@Google", "type": "entity", "priority": 1},
    "NVIDIA": {"handle": "@nvidia", "type": "entity", "priority": 1},
    "Nvidia": {"handle": "@nvidia", "type": "entity", "priority": 1},
    "Tesla": {"handle": "@Tesla", "type": "entity", "priority": 1},
    "SpaceX": {"handle": "@SpaceX", "type": "entity", "priority": 1},
}

# Lowercased lookup for case-insensitive matching
_LOOKUP: dict[str, str] = {k.lower(): k for k in REGISTRY}

# Regex to find @mentions in post text
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{1,15})\b")

# ── Public helpers ───────────────────────────────────

def get_registry() -> dict[str, dict]:
    return REGISTRY


def get_allowed_handles() -> set[str]:
    """Set of allowed handle strings like '@BBCNews' (case-sensitive as stored)."""
    return {v["handle"] for v in REGISTRY.values()}


def get_allowed_handles_lower() -> set[str]:
    return {h.lower() for h in get_allowed_handles()}


def resolve_handle(entity: str) -> str | None:
    """Return handle for an entity name, or None if not verified."""
    if not entity:
        return None
    key = _LOOKUP.get(entity.strip().lower())
    if key:
        return REGISTRY[key]["handle"]
    return None


def find_handles_for_story(story: dict) -> list[str]:
    """
    Suggest verified handles relevant to a story.
    Checks source names, cluster sources, and title/subject keywords.
    Returns deduped list, priority 1 first, max 4 candidates.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    # Source-based
    for field in ("source", "cluster_sources", "sources"):
        val = story.get(field)
        if isinstance(val, str):
            h = resolve_handle(val)
            if h and h not in seen:
                candidates.append(h)
                seen.add(h)
        elif isinstance(val, (list, tuple)):
            for item in val:
                if isinstance(item, str):
                    h = resolve_handle(item)
                    if h and h not in seen:
                        candidates.append(h)
                        seen.add(h)

    # Title/subject keyword scan — look for known entity names in title
    title = story.get("title", "") or ""
    title_low = title.lower()
    for name, info in REGISTRY.items():
        if name.lower() in title_low:
            h = info["handle"]
            if h not in seen:
                candidates.append(h)
                seen.add(h)

    # Sort by priority (1 first), then by order found
    def prio(h: str) -> int:
        for info in REGISTRY.values():
            if info["handle"] == h:
                return info["priority"]
        return 99

    candidates.sort(key=prio)
    return candidates[:4]


def extract_mentions(post: str) -> list[str]:
    """Extract @mentions from post text (with @)."""
    return [f"@{m}" for m in _MENTION_RE.findall(post)]


def validate_mentions(post: str) -> tuple[bool, str]:
    """
    Check that every @mention in post is from the verified registry.
    Returns (ok, reason). Allows 0 mentions.
    """
    mentions = extract_mentions(post)
    if not mentions:
        return True, "no mentions"
    allowed_lower = get_allowed_handles_lower()
    allowed_map = {h.lower(): h for h in get_allowed_handles()}
    for m in mentions:
        if m.lower() not in allowed_lower:
            return False, f"unverified handle {m!r} — not in registry"
    if len(mentions) > 2:
        return False, f"too many mentions ({len(mentions)} > 2)"
    return True, "ok"


def format_registry_for_prompt(max_entries: int = 20) -> str:
    """Compact registry listing for AI prompt."""
    lines = ["Verified X handles you MAY use (if relevant). Never invent others:"]
    for name, info in list(REGISTRY.items())[:max_entries]:
        lines.append(f"- {name} → {info['handle']} ({info['type']})")
    lines.append("If a source/entity isn't listed, write its plain name (e.g., 'The New York Times reports...').")
    return "\n".join(lines)
