"""Scheduling — rolling queue, gap bounds, horizon, breaking fast-path."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app import config
from app.logging_setup import get_logger

log = get_logger("x-news-bot.scheduler")


def _to_bot_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(config.BOT_TIMEZONE)


def _posting_window_for_day(day: datetime) -> tuple[datetime, datetime]:
    """Return (window_start, window_end) in BOT_TIMEZONE for the given day."""
    w_start = day.replace(hour=config.POSTING_START_HOUR, minute=0, second=0, microsecond=0)
    w_end = day.replace(hour=config.POSTING_END_HOUR, minute=0, second=0, microsecond=0)
    return w_start, w_end


def is_within_posting_window(now: datetime) -> bool:
    bot_now = _to_bot_tz(now)
    h = bot_now.hour + bot_now.minute / 60
    return config.POSTING_START_HOUR <= h < config.POSTING_END_HOUR


def is_within_active_window(now: datetime) -> bool:
    bot_now = _to_bot_tz(now)
    hm = bot_now.hour * 60 + bot_now.minute
    start = config.ACTIVE_START_H * 60 + config.ACTIVE_START_M
    end = config.ACTIVE_END_H * 60 + config.ACTIVE_END_M
    if start <= end:
        return start <= hm < end
    # overnight wrap (e.g. 22:00-06:00)
    return hm >= start or hm < end


def _next_slot_after(
    earliest: datetime,
    last_scheduled: datetime | None,
    is_breaking: bool,
) -> datetime:
    """Compute the next dueAt respecting gaps and posting window."""
    if is_breaking:
        # Breaking: earliest feasible slot (now + small delay), window-aware
        due = earliest + timedelta(minutes=config.BREAKING_MIN_DELAY_MINUTES)
        # If due is outside posting window and breaking not allowed outside, push to next window
        if not config.ALLOW_BREAKING_OUTSIDE_WINDOW and not is_within_posting_window(due):
            bot_due = _to_bot_tz(due)
            # push to next day's posting start
            next_day = (bot_due + timedelta(days=1)).replace(hour=config.POSTING_START_HOUR, minute=2, second=0, microsecond=0)
            due = next_day.astimezone(timezone.utc)
        return due

    # Normal: respect gap from last scheduled post
    if last_scheduled:
        gap = random.randint(config.MIN_NORMAL_GAP_MINUTES, config.MAX_NORMAL_GAP_MINUTES)
        candidate = max(earliest, last_scheduled + timedelta(minutes=gap))
    else:
        # First post in queue: at least 10 min future so Buffer never rejects and AI latency is safe
        offset = random.randint(10, max(15, config.MIN_NORMAL_GAP_MINUTES))
        candidate = earliest + timedelta(minutes=offset)

    # Clamp to posting window
    if not is_within_posting_window(candidate):
        bot_cand = _to_bot_tz(candidate)
        w_start, w_end = _posting_window_for_day(bot_cand)
        if bot_cand >= w_end:
            # past window → next day start
            next_start = (w_end + timedelta(days=1)).replace(hour=config.POSTING_START_HOUR, minute=5, second=0, microsecond=0)
            candidate = next_start.astimezone(timezone.utc)
        elif bot_cand < w_start:
            candidate = w_start.astimezone(timezone.utc)

    return candidate


def compute_schedule(
    stories: list[dict],
    existing_scheduled: list[dict] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """
    Assign dueAt to stories. Returns list of {story, due_at, is_breaking}.

    existing_scheduled: already-queued posts with dueAt (datetime aware UTC), sorted ascending.
    Respects MAX_BUFFER_AHEAD_POSTS (soft limit; breaking may exceed by 1), horizon, and posting window.
    Stories should be pre-sorted by priority (breaking first).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    existing_scheduled = existing_scheduled or []
    # Sort existing by dueAt
    existing_scheduled = sorted(existing_scheduled, key=lambda p: p.get("dueAt") or p.get("scheduled_at") or "")

    horizon = now + timedelta(minutes=config.MAX_SCHEDULE_HORIZON_MINUTES)
    # Determine last scheduled dueAt in UTC
    last_due: datetime | None = None
    for p in existing_scheduled:
        raw = p.get("dueAt") or p.get("scheduled_at")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            if last_due is None or dt > last_due:
                last_due = dt
        except Exception:
            continue

    queue_count = len(existing_scheduled)
    results: list[dict] = []

    for story in stories:
        is_breaking = story.get("is_breaking", False) or story.get("format") in ("BREAKING", "DEVELOPING")
        # Soft cap: don't schedule normal posts if queue already at MAX_BUFFER_AHEAD
        if not is_breaking and queue_count >= config.MAX_BUFFER_AHEAD_POSTS:
            log.info("Queue at capacity (%d/%d), deferring normal story %s", queue_count, config.MAX_BUFFER_AHEAD_POSTS, story.get("story_id", "?"))
            continue

        due = _next_slot_after(now, last_due, is_breaking)

        # Horizon check (breaking may slightly exceed horizon; normal must not)
        if not is_breaking and due > horizon:
            log.info("Story %s due %s beyond horizon %s, deferring", story.get("story_id"), due.isoformat(), horizon.isoformat())
            continue

        # Posting window check already handled in _next_slot_after; but double-gate for normal stories outside window
        if not is_breaking and not is_within_posting_window(due):
            log.info("Story %s due outside posting window, deferring", story.get("story_id"))
            continue

        results.append({"story": story, "due_at": due, "is_breaking": is_breaking})
        last_due = due
        queue_count += 1

    return results


def format_due_at(dt: datetime) -> str:
    """ISO 8601 UTC with millis for Buffer API (required format)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
