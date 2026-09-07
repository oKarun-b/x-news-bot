"""Quality control — pre-Buffer gate. JUST IN brand, flags, mentions, no URL, 280 hard."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app import config, editorial
from app.logging_setup import get_logger
from app.normalize import weighted_length

log = get_logger("x-news-bot.validate")

# ── Hard truncation only as emergency safeguard ──────
def _emergency_truncate(text: str, hard_max: int) -> str:
    if weighted_length(text) <= hard_max:
        return text
    truncated = text[: hard_max - 1].rstrip()
    m = re.search(r".*[.!?]\s", truncated)
    if m:
        cut = m.group(0).rstrip()
        if weighted_length(cut) <= hard_max and len(cut) > hard_max * 0.6:
            return cut
    last_space = truncated.rfind(" ")
    if last_space > hard_max * 0.5:
        truncated = truncated[:last_space].rstrip()
    return truncated + "…"


# ── Prohibited visible labels (old brand) ────────────
# Flag old multi-word labels anywhere, and emoji+single-word labels. Don't flag normal words like "context" alone.
_PROHIBITED_RE = re.compile(r"(NEWS UPDATE|BREAKING NEWS|KEY DETAIL|📰\s*NEWS UPDATE|🚨\s*BREAKING NEWS|⚡\s*DEVELOPING|🔎\s*CONTEXT|📌\s*KEY DETAIL)", re.IGNORECASE)

# ── Boilerplate Phrases ──────────────────────────────
_BOILERPLATE_PHRASES = [
    "this marks a significant development",
    "this comes amid",
    "sparked widespread debate",
    "could have major implications",
    "in a major development",
    "experts say this could",
    "this could have major",
    "significant development",
]

def _contains_boilerplate(text: str) -> str | None:
    low = text.lower()
    for phrase in _BOILERPLATE_PHRASES:
        if phrase in low:
            return phrase
    return None


def validate_post(
    post: str,
    selected_format: str,
    story: dict | None = None,
    existing_post_texts: set[str] | None = None,
    ai_generate_fn=None,
) -> tuple[bool, str, str]:
    """
    Validate final post (after flags + mentions inserted).
    Returns (ok, final_post, reason).
    """
    if not post or not post.strip():
        return False, post, "empty post"

    stripped = post.strip()

    # ── JUST IN: check (after optional 0-2 flags) ────
    # Flags are 2-char regional indicators, count them
    from app.countries import count_flags, contains_url as _contains_url
    flag_count = count_flags(stripped)
    if flag_count > 2:
        return False, post, f"too many flags ({flag_count} > 2)"
    # Remove leading flags + space to check JUST IN:
    without_flags = stripped
    # Flags are at start, each flag is 2 code units but 1 grapheme — our regex finds them
    # Strip them sequentially
    import re as _re
    _flag_re = _re.compile(r"^([\U0001F1E6-\U0001F1FF]{2}\s*)+")
    m = _flag_re.match(without_flags)
    if m:
        without_flags = without_flags[m.end():].lstrip()
    if not without_flags.startswith("JUST IN:"):
        return False, post, "post must start with JUST IN: after optional flags"
    # Ensure "JUST IN:" is followed by space and content
    after_just_in = without_flags[len("JUST IN:"):].strip()
    if not after_just_in or len(after_just_in) < 5:
        return False, post, "JUST IN: must be followed by content"

    # ── Prohibited old labels ────────────────────────
    if _PROHIBITED_RE.search(post):
        return False, post, "contains prohibited visible label (NEWS UPDATE/BREAKING NEWS etc)"

    # ── No URLs ──────────────────────────────────────
    if _contains_url(post):
        return False, post, "post must not contain URLs"

    # ── Breaking freshness ───────────────────────────
    if selected_format in ("BREAKING", "DEVELOPING") and story:
        pub = story.get("published_at")
        if pub is not None:
            try:
                now = datetime.now(timezone.utc)
                age_min = (now - pub).total_seconds() / 60
                if age_min > config.BREAKING_MAX_AGE_MINUTES:
                    return False, post, f"breaking story too old ({age_min:.0f} min > {config.BREAKING_MAX_AGE_MINUTES})"
            except Exception:
                pass

    # ── Duplicate ────────────────────────────────────
    if existing_post_texts and post.strip() in existing_post_texts:
        return False, post, "duplicate post text"

    wl = weighted_length(post)

    # ── Hard max 280 ─────────────────────────────────
    if wl > 280:
        if ai_generate_fn is not None and wl <= 400:
            try:
                retry = ai_generate_fn(story, selected_format, post, wl)
                if retry:
                    wl2 = weighted_length(retry)
                    if wl2 <= 280:
                        ok, final, reason = validate_post(retry, selected_format, story, existing_post_texts, ai_generate_fn=None)
                        if ok:
                            return True, final, "rewritten"
                        return False, retry, reason
                    truncated = _emergency_truncate(retry, 280)
                    if weighted_length(truncated) <= 280 and len(truncated) > 280 * 0.5:
                        log.warning("Post hard-truncated %d→%d chars (emergency after rewrite)", wl2, weighted_length(truncated))
                        return True, truncated, "hard-truncated"
            except Exception as exc:
                log.warning("Rewrite attempt failed: %s", exc)
        return False, post, f"exceeds hard limit {wl} > 280"

    # ── Preferred 100-220 (§9: warn but accept up to hard 280) ──
    if wl < config.PREFERRED_POST_MIN:
        log.warning("Post %d chars below preferred %d-%d — accepting", wl, config.PREFERRED_POST_MIN, config.PREFERRED_POST_MAX)
    elif wl > config.PREFERRED_POST_MAX:
        log.warning("Post %d chars above preferred %d-%d (≤280 allowed) — accepting", wl, config.PREFERRED_POST_MIN, config.PREFERRED_POST_MAX)

    # ── Hashtags (§15: zero by default) ──────────────
    if not config.ALLOW_HASHTAGS:
        _tag_re = re.compile(r"#\w+")
        m = _tag_re.search(post)
        if m:
            return False, post, f"contains hashtag {m.group(0)!r} (disabled by policy)"

    # ── Mention validation ───────────────────────────
    from app.accounts import validate_mentions
    ok_m, reason_m = validate_mentions(post)
    if not ok_m:
        return False, post, f"mention violation: {reason_m}"

    # ── Paragraph check (1-2, no unnecessary second) ─
    paragraphs = [p.strip() for p in post.split("\n\n") if p.strip()]
    # Also handle single newlines as paragraph breaks for lenient check
    if len(paragraphs) > 2:
        return False, post, f"too many paragraphs ({len(paragraphs)} > 2)"
    # If 2 paragraphs, second should not be trivial filler
    if len(paragraphs) == 2 and len(paragraphs[1]) < 15:
        return False, post, "second paragraph too short / filler"

    # ── Boilerplate ──────────────────────────────────
    boiler = _contains_boilerplate(post)
    if boiler:
        return False, post, f"contains AI boilerplate: {boiler!r}"

    # ── Basic malformed ──────────────────────────────
    if len(post.strip()) < 20:
        return False, post, "post too short"

    return True, post, "ok"


def check_daily_limits(day_counts: dict, kind: str = "ai") -> tuple[bool, str]:
    """Check if daily limits allow another post. kind: 'ai' or 'fixed'."""
    ai_scheduled = day_counts.get("ai_scheduled", 0)
    total = day_counts.get("total", 0)
    if kind == "ai" and ai_scheduled >= config.MAX_AI_POSTS_PER_DAY:
        return False, f"AI daily limit reached ({ai_scheduled}/{config.MAX_AI_POSTS_PER_DAY})"
    if total >= config.MAX_TOTAL_POSTS_PER_DAY:
        return False, f"Total daily limit reached ({total}/{config.MAX_TOTAL_POSTS_PER_DAY})"
    return True, "ok"
