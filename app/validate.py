"""Quality control — pre-Buffer gate. Length rewrite loop, format/age rules, dupes, limits."""
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
    # Truncate on word boundary, never mid-sentence silently if avoidable.
    # Find last sentence boundary before limit.
    truncated = text[: hard_max - 1].rstrip()
    # Try to cut at last ". " or "! " or "? "
    m = re.search(r".*[.!?]\s", truncated)
    if m:
        cut = m.group(0).rstrip()
        if weighted_length(cut) <= hard_max and len(cut) > hard_max * 0.6:
            return cut
    # Fallback: word boundary + ellipsis
    last_space = truncated.rfind(" ")
    if last_space > hard_max * 0.5:
        truncated = truncated[:last_space].rstrip()
    return truncated + "…"


def validate_post(
    post: str,
    selected_format: str,
    story: dict | None = None,
    existing_post_texts: set[str] | None = None,
    ai_generate_fn=None,
) -> tuple[bool, str, str]:
    """
    Validate a generated post.
    If ai_generate_fn is provided and post is 261-280, attempt one AI rewrite.

    Returns (ok, final_post, reason). If ok is False, reason explains rejection.
    """
    if not post or not post.strip():
        return False, post, "empty post"

    # Format prefix check
    expected_label = editorial.FORMAT_LABELS.get(selected_format, "")
    if expected_label and not post.strip().startswith(expected_label):
        return False, post, f"missing format prefix {expected_label!r}"

    # Breaking freshness check
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

    # Duplicate post text
    if existing_post_texts and post.strip() in existing_post_texts:
        return False, post, "duplicate post text"

    wl = weighted_length(post)

    if wl > config.HARD_MAX_POST_LENGTH:
        # Never publish >280. Try AI rewrite if available; otherwise reject (hard-truncate only if rewrite was attempted).
        if ai_generate_fn is not None and wl <= 400:
            try:
                retry = ai_generate_fn(story, selected_format, post, wl)
                if retry:
                    wl2 = weighted_length(retry)
                    if wl2 <= config.HARD_MAX_POST_LENGTH:
                        ok, final, reason = validate_post(retry, selected_format, story, existing_post_texts, ai_generate_fn=None)
                        if ok:
                            return True, final, "rewritten"
                        return False, retry, reason
                    # Emergency truncate only after a failed rewrite attempt
                    truncated = _emergency_truncate(retry, config.HARD_MAX_POST_LENGTH)
                    if weighted_length(truncated) <= config.HARD_MAX_POST_LENGTH and len(truncated) > config.HARD_MAX_POST_LENGTH * 0.5:
                        log.warning("Post hard-truncated %d→%d chars (emergency after rewrite)", wl2, weighted_length(truncated))
                        return True, truncated, "hard-truncated"
            except Exception as exc:
                log.warning("Rewrite attempt failed: %s", exc)
        return False, post, f"exceeds hard limit {wl} > {config.HARD_MAX_POST_LENGTH}"

    if wl > config.MAX_POST_LENGTH:
        # 261-280: try AI rewrite if available, otherwise accept with warning (spec: prefer <=260)
        if ai_generate_fn is not None:
            try:
                retry = ai_generate_fn(story, selected_format, post, wl)
                if retry:
                    wl2 = weighted_length(retry)
                    if wl2 <= config.MAX_POST_LENGTH:
                        return True, retry, "rewritten"
                    if wl2 <= config.HARD_MAX_POST_LENGTH:
                        log.warning("Post %d chars after rewrite (still >%d but ≤%d) — accepting with warning", wl2, config.MAX_POST_LENGTH, config.HARD_MAX_POST_LENGTH)
                        return True, retry, "accepted-with-warning"
                    # still too long → reject
                    return False, retry, f"still too long after rewrite ({wl2})"
            except Exception as exc:
                log.warning("Rewrite attempt failed: %s", exc)
        log.warning("Post %d chars exceeds preferred %d but ≤%d — accepting with warning", wl, config.MAX_POST_LENGTH, config.HARD_MAX_POST_LENGTH)
        return True, post, "accepted-with-warning"

    # Mention validation (verified registry only, 0-2, counts toward limit)
    from app.accounts import validate_mentions
    ok_m, reason_m = validate_mentions(post)
    if not ok_m:
        return False, post, f"mention violation: {reason_m}"

    # Basic malformed checks
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
