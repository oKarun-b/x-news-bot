import json
import pathlib
import tempfile
from datetime import datetime, timezone, timedelta

from app.database import StateStore, _today_key
from app import config


def test_load_creates_default(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    p = tmp_path / "state.json"
    s = StateStore(p)
    s.load()
    assert s.data["version"] == 1
    assert "articles" in s.data


def test_upsert_articles(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    p = tmp_path / "s.json"
    s = StateStore(p)
    s.load()
    arts = [{"id": "a1", "title": "Hello", "canonical_url": "https://example.com/a", "source": "BBC", "tier": 1, "category": "world", "published": "", "feed_name": "BBC"}]
    counts = s.upsert_articles(arts)
    assert counts["new"] == 1
    # second upsert same id immediately → not counted as updated (churn reduction: <1h)
    counts2 = s.upsert_articles(arts)
    assert counts2["new"] == 0
    assert counts2["updated"] == 0
    # after 2 hours, it should count as updated
    from datetime import timedelta
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    counts3 = s.upsert_articles(arts, now=later)
    assert counts3["updated"] == 1


def test_daily_counts(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    p = tmp_path / "s.json"
    s = StateStore(p)
    s.load()
    day = "2024-01-10"
    assert s.daily_counts_for(day)["total"] == 0
    s.increment_daily(day, "ai_scheduled", 2)
    assert s.daily_counts_for(day)["ai_scheduled"] == 2
    assert s.daily_counts_for(day)["total"] == 2
    cap = s.remaining_capacity(day)
    assert cap["ai_remaining"] == config.MAX_AI_POSTS_PER_DAY - 2


def test_prune_removes_old(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    p = tmp_path / "s.json"
    s = StateStore(p)
    s.load()
    old = (datetime.now(timezone.utc) - timedelta(days=config.STATE_RETENTION_DAYS + 1)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    s.data["articles"] = {
        "old": {"last_seen_at": old, "canonical_url": "https://example.com/old", "title": "Old"},
        "new": {"last_seen_at": recent, "canonical_url": "https://example.com/new", "title": "New"},
    }
    s.data["posts"] = [
        {"created_at": old, "text": "old"},
        {"created_at": recent, "text": "new"},
    ]
    removed = s.prune()
    assert removed["articles"] == 1
    assert "old" not in s.data["articles"]
    assert len(s.data["posts"]) == 1


def test_save_no_change_returns_false(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    p = tmp_path / "s.json"
    s = StateStore(p)
    s.load()
    assert s.save() is True
    # second save without mutation → false (updated_at excluded from comparison)
    assert s.save() is False
