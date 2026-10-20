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
    assert not ok and ("domain" in reason or "URLs" in reason)


def test_clean_post_passes_attribution():
    post = "🇺🇸 JUST IN: Trump signed the order today. The Pentagon confirmed three carriers will reposition to the region this week."
    ok, _, reason = validate_post(post, "NEWS_UPDATE", _story())
    assert ok, reason


def test_story_specific_source_name_is_rejected():
    post = "JUST IN: The Daily Herald says the policy changes take effect next month."
    ok, _, reason = validate_post(post, "NEWS_UPDATE", {"title": "Policy change", "sources": ["The Daily Herald"]})
    assert not ok and "source" in reason


# ── Curated image library ────────────────────────────

def _write_index(tmp: pathlib.Path, idx: dict):
    d = tmp / "data" / "images"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(json.dumps(idx), encoding="utf-8")


def test_match_slugs_priority(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    _write_index(tmp_path, {
        "trump": {"keywords": ["trump"], "priority": 1, "files": ["t1.jpg"], "licenses": {"t1.jpg": "CC0 1.0"}},
        "us": {"keywords": ["washington"], "priority": 3, "files": ["us1.jpg"], "licenses": {"us1.jpg": "Public domain"}},
    })
    with patch.object(config, "DATA_DIR", tmp_path / "data"):
        import importlib
        import app.images as im
        importlib.reload(im)
        slugs = im.match_slugs({"title": "Trump speaks in Washington", "summary": ""})
    assert slugs[0] == "trump"


def test_select_rotation_no_repeat(tmp_path=pathlib.Path(tempfile.mkdtemp())):
    _write_index(tmp_path, {"trump": {"keywords": ["trump"], "priority": 1, "files": ["a.jpg", "b.jpg"], "licenses": {"a.jpg": "CC0", "b.jpg": "Public domain"}}})
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
    _write_index(tmp_path, {"netanyahu": {"keywords": ["netanyahu"], "priority": 1, "files": ["n1.jpg"], "licenses": {"n1.jpg": "CC0"}}})
    with patch.object(config, "DATA_DIR", tmp_path / "data"):
        with patch.object(config, "IMAGE_BASE_URL", "https://raw.githubusercontent.com/oKarun-b/x-news-bot/main/data/images"):
            import importlib
            import app.images as im
            importlib.reload(im)
            url = im.select_curated_image({"title": "Netanyahu speaks", "summary": ""}, {})
    assert url and url.startswith("https://raw.githubusercontent.com/")
    assert url.endswith(".jpg")


def test_curated_attribution_required_assets_are_not_selected(tmp_path):
    _write_index(tmp_path, {
        "trump": {"keywords": ["trump"], "priority": 1, "files": ["by.jpg"], "licenses": {"by.jpg": "CC BY-SA 4.0"}}
    })
    with patch.object(config, "DATA_DIR", tmp_path / "data"):
        import importlib
        import app.images as im
        importlib.reload(im)
        assert im.select_curated_image({"title": "Trump news", "summary": ""}, {}) is None


def test_commons_image_accepts_cc0_and_retains_provenance():
    from app.images import search_commons_image
    payload = {"query": {"pages": {"1": {
        "title": "File:Donald Trump official portrait.jpg",
        "imageinfo": [{
            "thumburl": "https://upload.wikimedia.org/portrait.jpg?utm_source=commons.wikimedia.org&utm_content=thumbnail",
            "thumbwidth": 1600,
            "mime": "image/jpeg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Official_portrait.jpg",
            "extmetadata": {
                "LicenseShortName": {"value": "CC0 1.0"},
                "Artist": {"value": "Public office"},
            },
        }],
    }}}}
    response = type("Response", (), {
        "raise_for_status": lambda self: None,
        "json": lambda self: payload,
    })()
    with patch("app.images.requests.get", return_value=response):
        asset = search_commons_image({"title": "Trump announces policy"})
    assert asset["url"] == "https://upload.wikimedia.org/portrait.jpg"
    assert asset["license"] == "CC0 1.0"
    assert asset["source_url"].startswith("https://commons.wikimedia.org/")


def test_commons_image_skips_attribution_required_license():
    from app.images import search_commons_image
    payload = {"query": {"pages": {"1": {
        "title": "File:Donald Trump portrait.jpg",
        "imageinfo": [{
            "url": "https://upload.wikimedia.org/photo.jpg",
            "width": 1600,
            "mime": "image/jpeg",
            "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}},
        }],
    }}}}
    response = type("Response", (), {
        "raise_for_status": lambda self: None,
        "json": lambda self: payload,
    })()
    with patch("app.images.requests.get", return_value=response):
        assert search_commons_image({"title": "Trump announces policy"}) is None
