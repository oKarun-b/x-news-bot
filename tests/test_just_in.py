"""Tests for JUST IN: brand — flags, mentions, no URL, 280 hard, etc."""
from app.validate import validate_post
from app.normalize import weighted_length


def _story():
    return {"title": "Test", "source": "BBC", "published_at": None, "summary": "summary"}


def test_just_in_prefix_required():
    ok, _, reason = validate_post("Hello no prefix, this is a long enough post for testing", "NEWS_UPDATE", _story())
    assert not ok and "JUST IN" in reason
    ok, _, _ = validate_post("JUST IN: hello world, this is a sufficiently long post for testing purposes", "NEWS_UPDATE", _story())
    assert ok
    ok, _, _ = validate_post("🇺🇸 JUST IN: hello world, this is a sufficiently long post for testing purposes", "NEWS_UPDATE", _story())
    assert ok
    ok, _, _ = validate_post("🇺🇸🇷🇺 JUST IN: hello world, this is a sufficiently long post for testing purposes", "NEWS_UPDATE", _story())
    assert ok


def test_zero_one_two_flags():
    for post in ["JUST IN: hello", "🇺🇸 JUST IN: hello", "🇺🇸🇷🇺 JUST IN: hello"]:
        ok, _, _ = validate_post(post + " " + "x" * 30, "NEWS_UPDATE", _story())
        assert ok, post


def test_more_than_two_flags_rejected():
    ok, _, reason = validate_post("🇺🇸🇷🇺🇺🇦 JUST IN: hello " + "x" * 30, "NEWS_UPDATE", _story())
    assert not ok and "too many flags" in reason
    # Also via codes
    from app.countries import codes_to_flags
    try:
        codes_to_flags(["US", "RU", "UA"])
        assert False
    except ValueError:
        pass


def test_no_urls():
    ok, _, reason = validate_post("JUST IN: hello https://example.com", "NEWS_UPDATE", _story())
    assert not ok and "URL" in reason
    ok, _, reason = validate_post("JUST IN: hello www.example.com", "NEWS_UPDATE", _story())
    assert not ok
    ok, _, _ = validate_post("JUST IN: hello world", "NEWS_UPDATE", _story())
    assert ok


def test_280_hard_max():
    # 279 should pass
    post = "JUST IN: " + "a" * 270  # 9 + 270 = 279
    assert weighted_length(post) == 279
    ok, _, _ = validate_post(post, "NEWS_UPDATE", _story())
    assert ok
    # 281 should fail
    post2 = "JUST IN: " + "a" * 272  # 281
    assert weighted_length(post2) == 281
    ok, _, reason = validate_post(post2, "NEWS_UPDATE", _story())
    assert not ok and "hard limit" in reason


def test_verified_handles():
    ok, _, _ = validate_post("JUST IN: hello, @BBCNews reports.", "NEWS_UPDATE", _story())
    assert ok
    ok, _, reason = validate_post("JUST IN: hello @fakehandle123", "NEWS_UPDATE", _story())
    assert not ok and "unverified" in reason


def test_max_mention_limit():
    ok, _, reason = validate_post("JUST IN: hello @BBCNews @Reuters @AP", "NEWS_UPDATE", _story())
    assert not ok and "too many" in reason
    ok, _, _ = validate_post("JUST IN: hello @BBCNews @Reuters", "NEWS_UPDATE", _story())
    assert ok


def test_no_automatic_old_labels():
    for bad in ["NEWS UPDATE", "BREAKING NEWS"]:
        ok, _, reason = validate_post(f"JUST IN: hello {bad} here", "NEWS_UPDATE", _story())
        # The post contains the old label text elsewhere — should be rejected
        # Our prohibited check looks for those phrases anywhere
        assert not ok, f"should reject {bad}"
    # Internal format BREAKING should not auto-insert BREAKING NEWS label
    ok, final, _ = validate_post("JUST IN: hello world " + "x" * 20, "BREAKING", _story())
    assert ok
    assert "BREAKING NEWS" not in final
    assert final.startswith("JUST IN:") or final.startswith("🇺")


def test_optional_second_paragraph():
    ok, _, _ = validate_post("JUST IN: hello world, this is a sufficiently long first paragraph for testing. " + "x" * 20, "NEWS_UPDATE", _story())
    assert ok
    ok, _, _ = validate_post("JUST IN: hello world, this is a sufficiently long first paragraph.\n\nThe second paragraph adds meaningful context and is long enough to be valid for testing.", "NEWS_UPDATE", _story())
    assert ok
    ok, _, reason = validate_post("JUST IN: a\n\nb\n\nc extra text to make it long enough for length check", "NEWS_UPDATE", _story())
    assert not ok and "paragraph" in reason


def test_quote_allowed():
    ok, _, _ = validate_post('🇷🇺 JUST IN: Putin says talks will be "difficult."', "NEWS_UPDATE", _story())
    assert ok


def test_boilerplate_rejected():
    ok, _, reason = validate_post("JUST IN: hello This marks a significant development for the region. " + "x" * 20, "NEWS_UPDATE", _story())
    assert not ok and "boilerplate" in reason


def test_final_validation_after_flags_and_mentions():
    # Build a post that is 270 without flags, then add 2 flags (2 chars) + space = 273, still <280
    base = "JUST IN: " + "a" * 240
    assert weighted_length(base) == 249  # 9 + 240
    # With 2 flags, it becomes 251 + 8 for mention = 259
    with_flags = "🇺🇸🇷🇺 JUST IN: " + "a" * 240 + " @BBCNews"
    wl = weighted_length(with_flags)
    assert wl < 280
    ok, _, _ = validate_post(with_flags, "NEWS_UPDATE", _story())
    assert ok
    # Adding a URL should fail even though length is ok
    with_url = with_flags + " https://example.com"
    ok, _, reason = validate_post(with_url, "NEWS_UPDATE", _story())
    assert not ok and "URL" in reason
