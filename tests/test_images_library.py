"""Tests: curated image library + attribution removal."""
import json
import pathlib
import tempfile
from unittest.mock import patch

from app import config
from app.validate import validate_post


def _story():
    return {"title": "t", "published_at": None}


# ── Attribution guards ───────────────────────────────

def test_according_to_rejected():
    ok, _, reason = validate_post("JUST IN: According to Reuters, Trump signed the order today in Washington.", "NEWS_UPDATE", _story())
    assert not ok and "according to" in reason


def test_outlet_tail_rejected():
    post = "🇺🇸 JUST IN: Trump signed the order today, the Jerusalem Post reports. Officials confirmed the decision."
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert not ok and "outlet attribution" in reason


def test_dash_tail_rejected():
    post = "JUST IN: Trump signed the order today in a ceremony at the White House - The Jerusalem Post"
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert not ok and "'- Source' tail" in reason


def test_bare_domain_rejected():
    post = "JUST IN: The strike was confirmed by officials and first reported on yahoo.com earlier today."
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert not ok and "domain" in reason


def test_clean_post_passes_attribution():
    post = "🇺🇸 JUST IN: Trump signed the order today. The Pentagon confirmed three carriers will reposition to the region this week."
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert ok, reason


# ── Curated image library ────────────────────────────

def _write_index(tmp: pathlib.Path, idx: dict):
    d = tmp / "data" / "images"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(json.dumps(idx), encoding="utf-8")


def test_match_slugs_priority(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    _write_index(tmp_path, {
        "trump": {"keywords": ["trump"], "priority": 1, "files": ["t1.jpg"]},
        "us": {"keywords": ["washington"], "priority": 3, "files": ["us1.jpg"]},
    })
    with patch.object(config, "DATA_DIR", tmp_path / "data"):
        import importlib
        import app.images as im
        importlib.reload(im)
        slugs = im.match_slugs({"title": "Trump speaks in Washington", "summary": ""})
    assert slugs[0] == "trump"


def test_select_rotation_no_repeat(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    _write_index(tmp_path, {"trump": {"keywords": ["trump"], "priority": 1, "files": ["a.jpg", "b.jpg"]}})
    with patch.object(config, "DATA_DIR", tmp_path / "data"):
        with patch.object(config, "IMAGE_BASE_URL", "https://raw.example.com/main/data/images"):
            import importlib
            import app.images as im
            importlib.reload(im)
            state = {}
            u1 = im.select_curated_image({"title": "Trump announces plan", "summary": ""}, state)
            u2 = im.select_curated_image({"title": "Trump announces plan", "summary": ""}, state)
            u3 = im.select_curated_image({"title": "Trump announces plan", "summary": ""}, state)
    assert u1.endswith("a.jpg") or u1.endswith("b.jpg")
    assert u1 != u2
    assert u3 == u1  # wraps around


def test_select_no_files_returns_none(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    _write_index(tmp_path, {"trump": {"keywords": ["trump"], "priority": 1, "files": []}})
    with patch.object(config, "DATA_DIR", tmp_path / "data"):
        import importlib
        import app.images as im
        importlib.reload(im)
        assert im.select_curated_image({"title": "Trump news", "summary": ""}, {}) is None


def test_url_is_buffer_valid(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    _write_index(tmp_path, {"netanyahu": {"keywords": ["netanyahu"], "priority": 1, "files": ["n1.jpg"]}})
    with patch.object(config, "DATA_DIR", tmp_path / "data"):
        with patch.object(config, "IMAGE_BASE_URL", "https://raw.githubusercontent.com/oKarun-b/x-news-bot/main/data/images"):
            import importlib
            import app.images as im
            importlib.reload(im)
            url = im.select_curated_image({"title": "Netanyahu speaks", "summary": ""}, {})
    assert url and url.startswith("https://raw.githubusercontent.com/")
    assert url.endswith(".jpg")
