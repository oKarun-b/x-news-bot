"""Controlled Tests A/B/C — fresh normal, breaking priority, no-news cheap path."""
import pathlib
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from app.database import StateStore
from app.scheduler import compute_schedule
from app import config


def _fresh_article(title="Fresh normal story: major market move", minutes_ago=5, tier=1):
    now = datetime.now(timezone.utc)
    return {
        "id": "test_fresh_1",
        "title": title,
        "link": "https://example.com/fresh",
        "canonical_url": "https://example.com/fresh",
        "source": "BBC",
        "published": (now - timedelta(minutes=minutes_ago)).isoformat(),
        "published_at": now - timedelta(minutes=minutes_ago),
        "summary": "A major market move was reported with fresh details.",
        "category": "business",
        "tier": tier,
        "feed_name": "BBC Business",
    }


def test_A_fresh_normal_scheduled_short():
    """Test A: genuinely recent normal article → scheduled only short distance ahead."""
    from app.clustering import cluster_articles
    from app.rank import rank_clusters

    # Use a fixed time inside posting window (10:00 UTC) so window gate doesn't defer.
    now = datetime(2024, 1, 10, 10, 0, tzinfo=timezone.utc)
    article = _fresh_article(minutes_ago=2)
    # Fix published_at to match our fixed now
    article["published_at"] = now - timedelta(minutes=2)
    article["published"] = article["published_at"].isoformat()
    clusters = cluster_articles([article])
    assert len(clusters) == 1
    ranked = rank_clusters(clusters, now)
    assert ranked[0]["_score"] > 10  # above threshold

    # Simulate AI selecting it as NEWS_UPDATE, normal priority
    story = {"story_id": ranked[0]["cluster_id"], "format": "NEWS_UPDATE", "is_breaking": False, "urgency": 50}
    # Schedule with empty queue
    sched = compute_schedule([story], existing_scheduled=[], now=now)
    assert len(sched) == 1, "fresh normal must get a slot"
    due = sched[0]["due_at"]
    delta_min = (due - now).total_seconds() / 60
    # Normal gap 25-180, plus small offset for first post (5-25) → expect 5..180
    assert 4 <= delta_min <= config.MAX_SCHEDULE_HORIZON_MINUTES, f"delta {delta_min} not in [4, {config.MAX_SCHEDULE_HORIZON_MINUTES}]"
    # Must be well within horizon, not far future
    horizon = now + timedelta(minutes=config.MAX_SCHEDULE_HORIZON_MINUTES)
    assert due <= horizon
    print(f"Test A PASS: fresh normal scheduled in {delta_min:.1f} min (due {due.isoformat()})")


def test_B_breaking_priority_over_queued_normals():
    """Test B: breaking story while 2-3 normals queued → breaking gets earliest slot."""
    now = datetime.now(timezone.utc)
    # Existing queue: 3 normals
    existing = [
        {"dueAt": (now + timedelta(minutes=20)).isoformat()},
        {"dueAt": (now + timedelta(minutes=55)).isoformat()},
        {"dueAt": (now + timedelta(minutes=90)).isoformat()},
    ]
    # Breaking story
    breaking = {"story_id": "break_1", "format": "BREAKING", "is_breaking": True, "urgency": 100}

    # Also include a normal candidate to show breaking priority
    normal = {"story_id": "norm_1", "format": "NEWS_UPDATE", "is_breaking": False, "urgency": 50}

    # Schedule breaking alone — should be earliest
    sched_b = compute_schedule([breaking], existing_scheduled=existing, now=now)
    assert len(sched_b) == 1
    due_b = sched_b[0]["due_at"]
    delta_b = (due_b - now).total_seconds() / 60
    # Breaking delay 2 min
    assert 1.5 <= delta_b <= 8, f"breaking delta {delta_b} should be ~2 min"
    # Must be before the second queued normal (55 min)
    second_due = datetime.fromisoformat(existing[1]["dueAt"])
    assert due_b < second_due, "breaking should be before existing normals"

    # Schedule both breaking + normal together (breaking first in sorted order)
    # Our main sorts breaking first; simulate that
    both = [breaking, normal]
    # Queue at capacity 3 → normal should be deferred, breaking should still schedule
    sched_both = compute_schedule(both, existing_scheduled=existing, now=now)
    # At capacity MAX_BUFFER_AHEAD=3, normal is deferred, breaking bypasses
    assert any(s["story"]["story_id"] == "break_1" for s in sched_both), "breaking must be scheduled even at capacity"
    assert not any(s["story"]["story_id"] == "norm_1" for s in sched_both), "normal should be deferred at capacity"

    print(f"Test B PASS: breaking scheduled in {delta_b:.1f} min, normals deferred at capacity")


def test_C_no_news_zero_calls():
    """Test C: no eligible new stories → 0 OpenRouter + 0 Buffer calls, quick finish."""
    tmp = pathlib.Path(tempfile.mkdtemp()) / "state_c.json"
    store = StateStore(tmp)
    store.load()
    # Seed state with an article already seen
    now = datetime.now(timezone.utc)
    seen_article = _fresh_article(title="Old story already seen", minutes_ago=60)
    seen_article["id"] = "seen_1"
    seen_article["canonical_url"] = "https://example.com/seen"
    # Use the article's actual title so history hash matches
    store.upsert_articles([seen_article], now)
    store.save()

    # Now run main with a collect that returns the SAME article (history duplicate)
    # Mock collect to return duplicate, and mock AI/Buffer to count calls
    # We patch where main imports them: app.main.collect_articles etc? main imports at runtime
    # So patch app.main.* and app.news etc. But main.py does: from app.news import collect_articles at top,
    # so we need to patch app.main.collect_articles (already imported ref) or app.news.collect_articles both work.
    # We'll patch app.main.collect_articles to ensure main's reference is mocked.
    with patch("app.main.collect_articles", return_value=([seen_article], {"attempted": 1, "succeeded": 1, "failed": 0, "failed_details": [], "total_articles": 1})):
        with patch("app.main.StateStore", return_value=store):
            # Patch AI and Buffer at their origin so main's imports are mocked
            with patch("app.ai.editorial_select") as mock_edit:
                with patch("app.ai.generate_post") as mock_gen:
                    with patch("app.buffer.create_scheduled_post") as mock_buf_create:
                        with patch("app.buffer.verify_channel") as mock_verify:
                            with patch("app.buffer.get_scheduled_posts", return_value=[]):
                                from app.main import run
                                # Force dry-run to avoid Buffer, but we still count mocked calls (should be 0)
                                code = run(dry_run_cli=True, force=True)
                                assert mock_edit.call_count == 0, f"editorial_select called {mock_edit.call_count} times, expected 0"
                                assert mock_gen.call_count == 0, f"generate_post called {mock_gen.call_count} times, expected 0"
                                assert mock_buf_create.call_count == 0, f"Buffer create called {mock_buf_create.call_count} times, expected 0"
                                assert code == 0
    print("Test C PASS: 0 OpenRouter generation calls, 0 Buffer creates, quick finish")


if __name__ == "__main__":
    test_A_fresh_normal_scheduled_short()
    test_B_breaking_priority_over_queued_normals()
    test_C_no_news_zero_calls()
    print("All A/B/C passed")
