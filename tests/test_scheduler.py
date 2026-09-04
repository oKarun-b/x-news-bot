import random
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from app import config
from app.scheduler import compute_schedule, is_within_active_window, is_within_posting_window, format_due_at


def _story(sid, fmt="NEWS_UPDATE", breaking=False):
    return {"story_id": sid, "format": fmt, "is_breaking": breaking}


def _now_at(hour, minute=0):
    # Return a datetime in UTC that corresponds to BOT_TIMEZONE hour (BOT_TIMEZONE is UTC by default)
    return datetime(2024, 1, 10, hour, minute, tzinfo=timezone.utc)


def test_breaking_scheduled_soon():
    now = _now_at(10, 0)
    stories = [_story("b1", "BREAKING", True)]
    sched = compute_schedule(stories, existing_scheduled=[], now=now)
    assert len(sched) == 1
    due = sched[0]["due_at"]
    # breaking delay ~2 min
    delta = (due - now).total_seconds() / 60
    assert 1 <= delta <= 10


def test_normal_respects_gap():
    random.seed(0)
    now = _now_at(10, 0)
    existing = [{"dueAt": (now + timedelta(minutes=5)).isoformat()}]
    stories = [_story("n1")]
    sched = compute_schedule(stories, existing_scheduled=existing, now=now)
    assert len(sched) == 1
    due = sched[0]["due_at"]
    # Must be at least MIN_NORMAL_GAP after last scheduled
    last = datetime.fromisoformat(existing[0]["dueAt"])
    assert (due - last).total_seconds() / 60 >= config.MIN_NORMAL_GAP_MINUTES - 0.1


def test_queue_capacity_defers_normal():
    now = _now_at(10, 0)
    # Fill queue to MAX_BUFFER_AHEAD
    existing = [{"dueAt": (now + timedelta(minutes=i * 30)).isoformat()} for i in range(config.MAX_BUFFER_AHEAD_POSTS)]
    stories = [_story("n1"), _story("n2")]
    sched = compute_schedule(stories, existing_scheduled=existing, now=now)
    # Normal stories deferred when at capacity
    assert len(sched) == 0


def test_breaking_bypasses_capacity():
    now = _now_at(10, 0)
    existing = [{"dueAt": (now + timedelta(minutes=i * 30)).isoformat()} for i in range(config.MAX_BUFFER_AHEAD_POSTS)]
    stories = [_story("b1", "BREAKING", True)]
    sched = compute_schedule(stories, existing_scheduled=existing, now=now)
    assert len(sched) == 1
    assert sched[0]["is_breaking"] is True


def test_horizon_defers_far_normal():
    now = _now_at(10, 0)
    # Put last scheduled near horizon so next normal would exceed it
    horizon = now + timedelta(minutes=config.MAX_SCHEDULE_HORIZON_MINUTES)
    existing = [{"dueAt": (horizon - timedelta(minutes=5)).isoformat()}]
    stories = [_story("n1")]
    sched = compute_schedule(stories, existing_scheduled=existing, now=now)
    # Might be deferred if computed due exceeds horizon
    # With random gap 25-180, next slot will be horizon+20+ → deferred
    assert len(sched) == 0


def test_posting_window_check():
    # Within posting window (default 7-22 UTC)
    assert is_within_posting_window(_now_at(10, 0)) is True
    assert is_within_posting_window(_now_at(23, 0)) is False


def test_active_window():
    # Default active 06:00-23:30
    assert is_within_active_window(_now_at(10, 0)) is True
    assert is_within_active_window(_now_at(5, 0)) is False
    assert is_within_active_window(_now_at(23, 15)) is True
    assert is_within_active_window(_now_at(23, 45)) is False


def test_format_due_at():
    dt = datetime(2026, 3, 10, 15, 0, 0, tzinfo=timezone.utc)
    assert format_due_at(dt) == "2026-03-10T15:00:00.000Z"


def test_breaking_while_normals_scheduled():
    """TEST 3: breaking story inserts before/near next slot while normals exist."""
    now = _now_at(9, 10)
    existing = [
        {"dueAt": (now + timedelta(minutes=18)).isoformat()},   # 09:28
        {"dueAt": (now + timedelta(minutes=67)).isoformat()},   # 10:17
    ]
    # A breaking story should be scheduled soon (~09:12) even though queue has normals
    sched = compute_schedule([_story("break", "BREAKING", True)], existing_scheduled=existing, now=now)
    assert len(sched) == 1
    assert sched[0]["due_at"] < datetime.fromisoformat(existing[1]["dueAt"])
