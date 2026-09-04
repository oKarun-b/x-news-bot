from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from app import editorial
from app.validate import validate_post, check_daily_limits


def _story(published_at=None):
    return {"title": "Test", "source": "BBC", "published_at": published_at, "summary": "summary"}


def test_validate_missing_prefix_rejected():
    ok, _, reason = validate_post("Hello world this is a long enough post to pass length checks.", "BREAKING", _story())
    assert ok is False
    assert "prefix" in reason


def test_validate_breaking_old_rejected():
    old = datetime.now(timezone.utc) - timedelta(minutes=400)
    post = editorial.FORMAT_LABELS["BREAKING"] + " Something happened recently and this text is long enough to pass size checks."
    ok, _, reason = validate_post(post, "BREAKING", _story(old))
    assert ok is False
    assert "too old" in reason


def test_validate_duplicate_rejected():
    post = editorial.FORMAT_LABELS["NEWS_UPDATE"] + " Duplicate post text that is long enough to be valid news content."
    existing = {post}
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story(), existing_post_texts=existing)
    assert ok is False
    assert "duplicate" in reason


def test_validate_preferred_length_ok():
    post = editorial.FORMAT_LABELS["NEWS_UPDATE"] + " Short but sufficient news post content here."
    ok, final, _ = validate_post(post, "NEWS_UPDATE", _story())
    assert ok is True
    assert final == post


def test_validate_261_280_accepted_with_warning():
    from app.normalize import weighted_length
    prefix = editorial.FORMAT_LABELS["NEWS_UPDATE"] + " "
    # Build a post that is >260 but ≤280 weighted
    body_len = 261 - len(prefix) + 5
    body = "a" * body_len
    post = prefix + body
    wl = weighted_length(post)
    assert 261 <= wl <= 280, f"wl={wl}"
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert ok is True
    assert "warning" in reason


def test_validate_hard_limit_rejected():
    from app.normalize import weighted_length
    prefix = editorial.FORMAT_LABELS["NEWS_UPDATE"] + " "
    body_len = 281 - len(prefix) + 5
    body = "a" * body_len
    post = prefix + body
    assert weighted_length(post) > 280
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert ok is False


def test_validate_rewrite_attempt():
    from app.normalize import weighted_length
    prefix = editorial.FORMAT_LABELS["NEWS_UPDATE"] + " "
    body_len = 261 - len(prefix) + 10
    post = prefix + "a" * body_len
    assert weighted_length(post) > 260
    short = editorial.FORMAT_LABELS["NEWS_UPDATE"] + " Short rewritten post."
    def fake_rewrite(story, fmt, old_post, wl):
        return short
    ok, final, reason = validate_post(post, "NEWS_UPDATE", _story(), ai_generate_fn=fake_rewrite)
    assert ok is True
    assert final == short
    assert reason == "rewritten"


def test_daily_limits():
    ok, _ = check_daily_limits({"ai_scheduled": 8, "total": 8}, kind="ai")
    assert ok is False
    ok, _ = check_daily_limits({"ai_scheduled": 7, "total": 9}, kind="ai")
    assert ok is True  # ai slots left
    ok, _ = check_daily_limits({"ai_scheduled": 5, "total": 10}, kind="ai")
    assert ok is False  # total cap


# ── AI JSON extraction (no network) ─────────────────

def test_ai_extract_json_fenced():
    from app.ai import _extract_json
    assert _extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert _extract_json('{"post": "hi"}') == {"post": "hi"}


def test_ai_editorial_select_mocked():
    from app.ai import editorial_select
    fake_content = '[{"story_id": "s1", "decision": "select", "urgency": 75, "format": "NEWS_UPDATE", "reason": "important", "is_new_development": true}]'
    with patch("app.ai._chat", return_value=fake_content):
        out = editorial_select([{"story_id": "s1", "title": "T"}])
    assert out[0]["decision"] == "select"


def test_ai_generate_mocked():
    from app.ai import generate_post
    fake = '{"post": "📰 NEWS UPDATE Something happened", "confidence": 0.9}'
    with patch("app.ai._chat", return_value=fake):
        out = generate_post({"title": "T", "source": "BBC", "published": "now", "summary": "hi"}, "NEWS_UPDATE")
    assert out["post"].startswith("📰")
