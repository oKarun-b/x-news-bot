"""Regression fixtures inspired by screenshot failure modes (not factual reports)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app import config
from app.content import (central_country_codes, clean_headline, duplicate_development,
                         render_post, story_problem)
from app.database import StateStore, _today_key
from app.validate import validate_post


@pytest.fixture
def clock(monkeypatch):
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    now = Clock.fromtimestamp(now.timestamp(), timezone.utc)
    for module in ("app.main", "app.content", "app.validate", "app.publishing", "app.database"):
        monkeypatch.setattr(f"{module}.datetime", Clock)
    return now


def story(now, **overrides):
    return {
        "title": "Trump says talks with Zelensky may resume on Friday",
        "published_at": now - timedelta(minutes=5), "require_freshness": True,
        "sources": ["Test Wire"], "country_codes": ["US", "UA"], **overrides,
    }


@pytest.mark.parametrize("suffix", ["bloomingbit", "Democracy Now!", "9News", "Українські Національні Новини (УНН)"])
def test_publisher_suffix_removed_without_damaging_claim(suffix):
    title = "Minister says talks may resume - " + suffix
    assert clean_headline(title, [suffix]) == "Minister says talks may resume"
    assert clean_headline("Minister says talks may resume - terms remain disputed", [suffix]).endswith("terms remain disputed")


def test_labels_flags_and_claims(clock):
    evidence = story(clock)
    body = "Trump says talks with Zelensky may resume on Friday."
    text = render_post(body, ["US", "UA"], "NEWS_UPDATE", evidence["published_at"])
    assert text == "JUST IN: 🇺🇸🇺🇦 " + body
    assert validate_post(text, "NEWS_UPDATE", evidence)[0]
    assert not validate_post("JUST IN: 🇺🇸🇺🇦 Talks with Zelensky will resume on Friday.", "NEWS_UPDATE", evidence)[0]
    assert not validate_post("JUST IN: 🇺🇸🇺🇦 Trump says talks with Zelensky will resume on Friday.", "NEWS_UPDATE", evidence)[0]
    assert not validate_post("JUST IN: 🇺🇸🇺🇦 Zelensky says talks with Trump may resume on Friday.", "NEWS_UPDATE", evidence)[0]
    older = story(clock, published_at=clock - timedelta(hours=3))
    updated = render_post(body, [], "NEWS_UPDATE", older["published_at"])
    assert updated.startswith("UPDATE:")
    assert validate_post(updated, "NEWS_UPDATE", older)[0]
    assert render_post(body, [], "CONTEXT", evidence["published_at"]).startswith("CONTEXT:")


def test_third_flag_requires_three_explicit_participants(clock):
    title = "Trump says he may meet Putin and Xi Jinping"
    codes = central_country_codes(["US", "RU", "CN"], title)
    evidence = story(clock, title=title, country_codes=codes)
    text = render_post(title, codes, "NEWS_UPDATE", evidence["published_at"])
    assert text.startswith("JUST IN: 🇺🇸🇷🇺🇨🇳 ")
    assert validate_post(text, "NEWS_UPDATE", evidence)[0]
    assert central_country_codes(["US", "RU", "CN"], "Trump meets Putin") == ["US", "RU"]


@pytest.mark.parametrize("title,age", [("FILE PHOTO: Presidents attend meeting", 5), ("Policy announcement", 13 * 60), ("Policy announcement", -60)])
def test_ineligible_story_rejected(title, age, clock):
    assert story_problem(title, clock - timedelta(minutes=age), clock)
    assert story_problem("Policy announcement", None, clock)


def test_same_event_duplicate_but_changed_figure_or_denial_survives():
    first = "Israeli police arrest suspects after settler attacks on European diplomats"
    second = "Israeli police arrest several suspects after settler attacks on European diplomats"
    assert duplicate_development(first, second)
    assert not duplicate_development("Police arrest 8 suspects in attacks", "Police arrest 12 suspects in attacks")
    assert not duplicate_development("Minister approves peace agreement", "Minister denies approving peace agreement")


def setup_pipeline(monkeypatch, tmp_path, now):
    from app.main import run
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.data["buffer_cache"] = {"channel_id": "ch", "organization_id": "org"}
    store.save()
    article = {
        "id": "fixture-a", "title": story(now)["title"], "source": "Test Wire",
        "link": "https://example.com/story", "canonical_url": "https://example.com/story",
        "published": (now - timedelta(minutes=5)).isoformat(),
        "published_at": now - timedelta(minutes=5), "summary": story(now)["title"],
        "category": "us", "tier": 1,
    }
    monkeypatch.setattr("app.main.StateStore", lambda: store)
    monkeypatch.setattr("app.main.collect_articles", lambda **kw: ([article], {
        "attempted": 1, "succeeded": 1, "failed": 0, "failed_details": [],
    }))
    monkeypatch.setattr("app.main.enrich_top_candidates", lambda *a: None)
    monkeypatch.setattr("app.ai.editorial_select", lambda candidates: [{
        "story_id": candidates[0]["story_id"], "decision": "select", "format": "BREAKING", "urgency": 100,
    }])
    monkeypatch.setattr("app.ai.generate_post", lambda *a, **kw: {"post": article["title"], "country_codes": ["US", "UA"]})
    monkeypatch.setattr(config, "EFFECTIVE_DRY_RUN", False)
    monkeypatch.setattr("app.buffer.get_scheduled_posts", lambda *a, **kw: [
        {"dueAt": (now + timedelta(minutes=30)).isoformat()},
        {"dueAt": (now + timedelta(minutes=90)).isoformat()},
    ])
    return run, store, article


def test_full_pipeline_two_portraits_and_breaking_before_routine_queue(monkeypatch, tmp_path, clock):
    run, store, article = setup_pipeline(monkeypatch, tmp_path, clock)
    monkeypatch.setattr("app.images.select_story_images", lambda *a, **kw: [
        {"url": "https://upload.wikimedia.org/trump.jpg", "metadata": {"license": "Public domain", "entity": "Donald Trump"}},
        {"url": "https://upload.wikimedia.org/zelensky.jpg", "metadata": {"license": "CC0", "entity": "Volodymyr Zelensky"}},
    ])
    gql = MagicMock(return_value={"createPost": {"post": {"id": "fixture-buffer-id"}}})
    monkeypatch.setattr("app.buffer._gql", gql)
    assert run(force=True) == 0
    gql.assert_called_once()
    query = gql.call_args.args[0]
    assert "JUST IN: 🇺🇸🇺🇦 Trump says" in query
    assert query.count("image: { url:") == 2
    assert "Test Wire" not in query and "example.com/story" not in query
    post = store.data["posts"][-1]
    assert datetime.fromisoformat(post["scheduled_at"]) < clock + timedelta(minutes=20)
    assert store.daily_counts_for(_today_key(clock))["image_posts"] == 1
    assert store.daily_counts_for(_today_key(clock))["total"] == 1
    assert store.data["candidate_pool"][0]["posted"] is True


@pytest.mark.parametrize("failure", ["caption", "old", "generation"])
def test_invalid_inputs_never_reach_buffer(monkeypatch, tmp_path, clock, failure):
    run, store, article = setup_pipeline(monkeypatch, tmp_path, clock)
    if failure == "caption":
        article["title"] = "FILE PHOTO: Trump attends meeting with Zelensky"
    elif failure == "old":
        article["published_at"] = clock - timedelta(hours=24)
    else:
        monkeypatch.setattr("app.ai.generate_post", MagicMock(side_effect=RuntimeError("model unavailable")))
    gql = MagicMock()
    monkeypatch.setattr("app.buffer._gql", gql)
    run(force=True)
    gql.assert_not_called()
    assert store.daily_counts_for(_today_key(clock))["total"] == 0


def test_image_rejection_falls_back_and_clears_ledger(monkeypatch, tmp_path, clock):
    run, store, article = setup_pipeline(monkeypatch, tmp_path, clock)
    monkeypatch.setattr("app.images.select_story_images", lambda *a: [{"url": "https://upload.wikimedia.org/photo.jpg", "metadata": {}}])
    gql = MagicMock(side_effect=[
        {"createPost": {"message": "Image asset could not be fetched"}},
        {"createPost": {"post": {"id": "text-only"}}},
    ])
    monkeypatch.setattr("app.buffer._gql", gql)
    assert run(force=True) == 0
    assert "assets:" in gql.call_args_list[0].args[0]
    assert "assets:" not in gql.call_args_list[1].args[0]
    assert store.data["posts"][-1]["image_urls"] == []
    assert store.daily_counts_for(_today_key(clock))["image_posts"] == 0


def test_deferred_pool_survives_no_slot(monkeypatch, tmp_path, clock):
    run, store, article = setup_pipeline(monkeypatch, tmp_path, clock)
    monkeypatch.setattr("app.images.select_story_images", lambda *a: [])
    monkeypatch.setattr("app.main.compute_schedule", lambda *a, **kw: [])
    gql = MagicMock()
    monkeypatch.setattr("app.buffer._gql", gql)
    assert run(force=True) == 0
    assert store.get_candidate_pool()
    assert not store.data["candidate_pool"][0]["posted"]
    gql.assert_not_called()


def test_empty_feed_reuses_deferred_candidate(monkeypatch, tmp_path, clock):
    run, store, article = setup_pipeline(monkeypatch, tmp_path, clock)
    from app.main import compute_schedule
    monkeypatch.setattr("app.images.select_story_images", lambda *a: [])
    monkeypatch.setattr("app.main.compute_schedule", lambda *a, **kw: [])
    assert run(force=True) == 0
    monkeypatch.setattr("app.main.compute_schedule", compute_schedule)
    monkeypatch.setattr("app.main.collect_articles", lambda **kw: ([], {
        "attempted": 1, "succeeded": 1, "failed": 0, "failed_details": [],
    }))
    gql = MagicMock(return_value={"createPost": {"post": {"id": "recovered"}}})
    monkeypatch.setattr("app.buffer._gql", gql)
    assert run(force=True) == 0
    gql.assert_called_once()
    assert store.data["candidate_pool"][0]["posted"]


def test_dry_run_previews_do_not_consume_candidate_or_quota(monkeypatch, tmp_path, clock):
    run, store, article = setup_pipeline(monkeypatch, tmp_path, clock)
    monkeypatch.setattr("app.images.select_story_images", lambda *a: [])
    gql = MagicMock()
    monkeypatch.setattr("app.buffer._gql", gql)
    assert run(dry_run_cli=True, force=True) == 0
    gql.assert_not_called()
    assert store.daily_counts_for(_today_key(clock))["total"] == 0
    assert store.get_candidate_pool()
    assert not store.data["candidate_pool"][0]["posted"]
