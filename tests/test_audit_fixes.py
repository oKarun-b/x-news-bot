"""Tests for Sep 9 audit fixes: first-person guard, suffix strip, cross-run dedupe, spacing guard."""
from app.validate import validate_post
from app.normalize import normalize_title
from app.clustering import jaccard


def _story():
    return {"title": "t", "published_at": None}


def test_first_person_outside_quotes_rejected():
    post = "JUST IN: Seven aid workers, including seven of my colleagues, were killed in Gaza today and more are at risk."
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert not ok and "first-person" in reason


def test_first_person_inside_quotes_allowed():
    post = '🇵🇸 JUST IN: An aid group said "my colleagues are gone" after the strike in Gaza that killed seven workers.'
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert ok, reason


def test_normal_post_no_false_positive():
    post = "🇺🇸 JUST IN: Trump ordered the Pentagon to review the Iran strike plans, officials said on Tuesday."
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert ok, reason


def test_suffix_strip_powers():
    assert normalize_title("US denies involvement in strikes - Breakingthenews.net") == "us denies involvement in strikes"
    assert normalize_title("Oil surges past $100 - yahoo.com") == "oil surges past 100"
    assert normalize_title("Judge blocks order - Anadolu Ajansı") == "judge blocks order"


def test_cross_run_title_dedupe_similarity():
    a = {"uk", "government", "defends", "ban", "trade", "israeli", "settlements"}
    b = {"uk", "government", "defends", "decision", "ban", "trade", "israeli", "settlements"}
    assert jaccard(a, b) >= 0.5  # would be flagged as same story
    c = {"iran", "strikes", "tankers", "gulf", "military"}
    assert jaccard(a, c) < 0.3  # different story passes
