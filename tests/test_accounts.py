from app.accounts import (
    REGISTRY, resolve_handle, extract_mentions, validate_mentions,
    find_handles_for_story, get_allowed_handles,
)
from app.validate import validate_post
from app import editorial


def test_registry_has_core_entries():
    assert "@BBCNews" in get_allowed_handles()
    assert "@elonmusk" in get_allowed_handles()
    assert "@WhiteHouse" in get_allowed_handles()


def test_resolve_handle():
    assert resolve_handle("BBC News") == "@BBCNews"
    assert resolve_handle("bbc news") == "@BBCNews"
    assert resolve_handle("Unknown Outlet") is None
    # Never invent
    assert resolve_handle("New York Times") == "@nytimes"  # is in registry
    assert resolve_handle("Random Blog XYZ") is None


def test_extract_mentions():
    assert extract_mentions("Hello @BBCNews and @elonmusk!") == ["@BBCNews", "@elonmusk"]
    assert extract_mentions("No mentions here") == []


def test_validate_mentions_ok():
    ok, _ = validate_mentions("Hello @BBCNews reports today.")
    assert ok
    ok, _ = validate_mentions("No mentions at all — fine.")
    assert ok


def test_validate_mentions_invented_rejected():
    ok, reason = validate_mentions("According to @fakehandle123 this happened.")
    assert not ok
    assert "unverified" in reason


def test_validate_mentions_too_many():
    ok, reason = validate_mentions("@BBCNews @Reuters @AP all report this.")
    assert not ok
    assert "too many" in reason


def test_find_handles_for_story():
    story = {"source": "BBC News", "title": "Elon Musk says xAI update coming", "cluster_sources": ["BBC News"]}
    handles = find_handles_for_story(story)
    assert "@BBCNews" in handles
    assert "@elonmusk" in handles


def test_post_with_verified_mention_passes():
    post = "📰 NEWS UPDATE Trump orders gray wolves removed from protections.\n\nThe move reverses federal protections for the species, @BBCNews reports. " + "x" * 80
    ok, _, _ = validate_post(post, "NEWS_UPDATE", story=None)
    # Should pass (if length ok, mentions valid)
    # Build a well-sized post
    post2 = "📰 NEWS UPDATE Trump orders gray wolves removed from the endangered species list. The move reverses federal protections, @BBCNews reports. Conservation groups said they will review the decision."
    assert len(post2) <= 280
    ok, final, _ = validate_post(post2, "NEWS_UPDATE")
    assert ok
    assert "@BBCNews" in final


def test_post_with_invented_handle_rejected():
    post = "📰 NEWS UPDATE Something happened according to @fakehandle123 today in Washington."
    ok, _, reason = validate_post(post, "NEWS_UPDATE")
    assert not ok
    assert "mention violation" in reason


def test_post_with_three_mentions_rejected():
    post = "📰 NEWS UPDATE Big news today @BBCNews @Reuters @AP all report this development in Washington. Extra text to reach length."
    ok, _, reason = validate_post(post, "NEWS_UPDATE")
    assert not ok


def test_character_count_includes_mentions():
    # Mention must count toward limit
    base = "📰 NEWS UPDATE " + "a" * 240 + " @BBCNews"
    from app.normalize import weighted_length
    wl = weighted_length(base)
    assert wl > 260  # should be over due to mention
    ok, _, reason = validate_post(base, "NEWS_UPDATE")
    # Should be accepted with warning (261-280) or rejected if >280, but mention is counted
    assert ok or "exceeds" in reason or "warning" in reason
