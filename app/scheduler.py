"""Scheduling — §18 breaking ASAP, §19 dynamic irregular gaps, §21 rolling horizon."""
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
    return hm >= start or hm < end


def _dynamic_gap(remaining: int, now: datetime) -> int:
    """
    §19/§20: irregular spacing sized so remaining posts fill the rest of the
    US/EU audience day (until POSTING_END). Bounded 25..150 min.
    """
    bot_now = _to_bot_tz(now)
    window_end = bot_now.replace(hour=config.POSTING_END_HOUR, minute=0, second=0, microsecond=0)
    minutes_left = max(45, int((window_end - bot_now).total_seconds() / 60))
    if remaining <= 0:
        remaining = 1
    ideal = minutes_left / remaining
    # jitter 0.6x - 1.15x ideal for natural variation
    gap = int(ideal * random.uniform(0.6, 1.15))
    return max(config.MIN_NORMAL_GAP_MINUTES, min(config.MAX_NORMAL_GAP_MINUTES, gap))


def _next_slot_after(
    earliest: datetime,
    last_scheduled: datetime | None,
    is_breaking: bool,
    remaining: int = 3,
) -> datetime:
    """§18: breaking → ASAP. Normal → dynamic gap + audience window."""
    if is_breaking:
        # §18: publish ASAP — 2-4 min out, bypasses audience-window waiting
        due = earliest + timedelta(minutes=random.randint(config.BREAKING_MIN_DELAY_MINUTES, config.BREAKING_MAX_DELAY_MINUTES))
        return due

    if last_scheduled:
        gap = _dynamic_gap(remaining, earliest)
        candidate = max(earliest + timedelta(minutes=config.BREAKING_MIN_DELAY_MINUTES), last_scheduled + timedelta(minutes=gap))
    else:
        # First post: modest offset so it clears Buffer's future requirement
        offset = random.randint(config.MIN_NORMAL_GAP_MINUTES, config.MIN_NORMAL_GAP_MINUTES + 15)
        candidate = earliest + timedelta(minutes=offset)

    # Clamp into posting window (audience hours); if past end → defer to next morning
    if not is_within_posting_window(candidate):
        bot_cand = _to_bot_tz(candidate)
        if bot_cand.hour >= config.POSTING_END_HOUR:
            next_start = (bot_cand + timedelta(days=1)).replace(
                hour=config.POSTING_START_HOUR, minute=random.randint(5, 25), second=0, microsecond=0
            )
            candidate = next_start.astimezone(timezone.utc)
        elif bot_cand.hour < config.POSTING_START_HOUR:
            candidate = bot_cand.replace(
                hour=config.POSTING_START_HOUR, minute=random.randint(5, 25), second=0, microsecond=0
            ).astimezone(timezone.utc)
    return candidate


def compute_schedule(
    stories: list[dict],
    existing_scheduled: list[dict] | None = None,
    now: datetime | None = None,
    remaining_quota: int | None = None,
) -> list[dict]:
    """
    Assign dueAt to stories. Returns list of {story, due_at, is_breaking}.
    remaining_quota drives §19 dynamic spacing.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    existing_scheduled = existing_scheduled or []
    existing_scheduled = sorted(existing_scheduled, key=lambda p: p.get("dueAt") or p.get("scheduled_at") or "")

    horizon = now + timedelta(minutes=config.BUFFER_HORIZON_MINUTES)
    last_due: datetime | None = None
    for p in existing_scheduled:
        raw = p.get("dueAt") or p.get("scheduled_at")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            if last_due is None or dt > last_due:
                last_due = dt
        except Exception:
            continue

    queue_count = len(existing_scheduled)
    results: list[dict] = []
    scheduled_this_run = 0
    batch_size = max(1, remaining_quota if remaining_quota is not None else len(stories))

    for story in stories:
        is_breaking = story.get("is_breaking", False) or story.get("format") in ("BREAKING", "DEVELOPING")
        # Hard quota guard: never exceed DAILY_POST_HARD_MAX (breaking included)
        if remaining_quota is not None and scheduled_this_run >= remaining_quota:
            log.info("Quota exhausted for this run (%d scheduled), stopping", scheduled_this_run)
            break
        # Rolling capacity: keep MAX_BUFFER_AHEAD queued (breaking exempt — §18)
        if not is_breaking and queue_count >= config.MAX_BUFFER_AHEAD_POSTS:
            log.info("Queue at capacity (%d/%d), deferring normal story %s", queue_count, config.MAX_BUFFER_AHEAD_POSTS, story.get("story_id", "?"))
            continue

        if is_breaking:
            # §18: first breaking → ASAP; subsequent breaking/developing staggered 10-15 min
            due = _next_slot_after(now, last_due, True)
            if scheduled_this_run > 0:
                stagger = now + timedelta(minutes=config.BREAKING_MAX_DELAY_MINUTES + (scheduled_this_run * random.randint(10, 15)))
                due = max(due, stagger)
        else:
            # Scale gaps so this batch fits inside the rolling horizon (§21)
            stories_left = max(1, batch_size - scheduled_this_run)
            horizon_span_min = max(30, int((horizon - now).total_seconds() / 60))
            saved_max = config.MAX_NORMAL_GAP_MINUTES
            config.MAX_NORMAL_GAP_MINUTES = min(saved_max, max(config.MIN_NORMAL_GAP_MINUTES, horizon_span_min // stories_left))
            due = _next_slot_after(now, last_due, False, remaining_quota or 3)
            config.MAX_NORMAL_GAP_MINUTES = saved_max

        if not is_breaking and due > horizon:
            log.info("Story %s due %s beyond %d min horizon, deferring", story.get("story_id"), due.isoformat(), config.BUFFER_HORIZON_MINUTES)
            continue
        if not is_breaking and not is_within_posting_window(due):
            log.info("Story %s due outside posting window, deferring", story.get("story_id"))
            continue

        results.append({"story": story, "due_at": due, "is_breaking": is_breaking})
        last_due = due
        queue_count += 1
        scheduled_this_run += 1

    return results


def format_due_at(dt: datetime) -> str:
    """ISO 8601 UTC with millis for Buffer API."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
