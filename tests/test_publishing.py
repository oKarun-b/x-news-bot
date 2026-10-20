"""Last-mile guards: delays, day boundaries and image quotas."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app import config
from app.database import StateStore, _today_key
from app.publishing import prepare_post, publish_post


def draft(now, **kw):
    return {
        "title": "Trump says talks may resume", "text": "JUST IN: 🇺🇸 Trump says talks may resume.",
        "format": "NEWS_UPDATE", "is_breaking": False, "sources": [], "country_codes": ["US"],
        "published_at": (now - timedelta(minutes=80)).isoformat(),
        "scheduled_at": (now + timedelta(minutes=30)).isoformat(),
        "image_urls": ["https://upload.wikimedia.org/a.jpg", "https://upload.wikimedia.org/b.jpg"],
        **kw,
    }


def test_label_demotes_when_publication_will_no_longer_be_fresh(tmp_path):
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    store = StateStore(tmp_path / "state.json")
    store.load()
    post = draft(now)
    assert len(prepare_post(post, store, [], now)) == 2
    assert post["text"].startswith("UPDATE: 🇺🇸")


def test_expiry_at_publication_cannot_be_bypassed_by_context(tmp_path):
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    store = StateStore(tmp_path / "state.json")
    store.load()
    post = draft(now, published_at=(now - timedelta(hours=12)).isoformat(), format="CONTEXT")
    with pytest.raises(ValueError, match="too old"):
        prepare_post(post, store, [], now)


def test_midnight_schedule_checks_destination_day_quota(tmp_path):
    now = datetime(2026, 10, 20, 23, 59, tzinfo=timezone.utc)
    store = StateStore(tmp_path / "state.json")
    store.load()
    due = now + timedelta(minutes=3)
    store.increment_daily(_today_key(due), "ai_scheduled", config.MAX_AI_POSTS_PER_DAY)
    post = draft(now, published_at=now.isoformat(), scheduled_at=due.isoformat(), is_breaking=True, format="BREAKING")
    with pytest.raises(ValueError, match="limit|maximum"):
        prepare_post(post, store, [], now)


def test_image_daily_cap_counts_posts_not_individual_photos(tmp_path):
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.increment_daily(_today_key(now), "image_posts", config.MAX_IMAGE_POSTS_PER_DAY)
    assert prepare_post(draft(now), store, [], now) is None


def test_future_rejection_retry_rechecks_day_and_collision(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.data["buffer_cache"] = {"channel_id": "ch"}
    post = draft(now, published_at=now.isoformat(), scheduled_at=now.isoformat(), is_breaking=True, format="BREAKING")
    send = MagicMock(side_effect=[RuntimeError("Buffer createPost failed: dueAt must be in the future"), {"id": "p"}])
    monkeypatch.setattr("app.buffer.create_scheduled_post", send)
    occupied = [now + timedelta(minutes=4), now + timedelta(minutes=90)]
    assert publish_post(post, store, occupied)["id"] == "p"
    due = datetime.fromisoformat(post["scheduled_at"])
    assert due >= occupied[0] + timedelta(minutes=6)
    assert due < occupied[1]
    assert post["day"] == _today_key(due)
