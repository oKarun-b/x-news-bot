"""§images tests — RSS media extraction, og:image, Buffer assets payload, daily image quota."""
from unittest.mock import MagicMock, patch

from app import config
from app.article import extract_og_image
from app.news import _extract_media_urls
from app.buffer import create_scheduled_post
from app.database import StateStore
import pathlib, tempfile


# ── RSS media extraction ─────────────────────────────

def test_extract_media_from_media_content():
    e = {"media_content": [{"url": "https://cdn.example.com/photo.jpg", "type": "image/jpeg"}]}
    assert _extract_media_urls(e) == ["https://cdn.example.com/photo.jpg"]


def test_extract_media_from_thumbnail_and_enclosure():
    e = {
        "media_thumbnail": [{"url": "https://img.example.com/t.png"}],
        "enclosures": [{"href": "https://img.example.com/full.jpg", "type": "image/jpeg"}],
    }
    urls = _extract_media_urls(e)
    assert "https://img.example.com/t.png" in urls
    assert "https://img.example.com/full.jpg" in urls


def test_extract_media_skips_non_image():
    e = {"media_content": [{"url": "https://cdn.example.com/video.mp4", "type": "video/mp4"}],
         "enclosures": [{"href": "https://x.example.com/a.zip", "type": "application/zip"}]}
    assert _extract_media_urls(e) == []


def test_extract_media_dedupes_and_caps2():
    e = {"media_content": [
        {"url": "https://c.example.com/a.jpg"}, {"url": "https://c.example.com/a.jpg"},
        {"url": "https://c.example.com/b.jpg"}, {"url": "https://c.example.com/c.jpg"},
    ]}
    urls = _extract_media_urls(e)
    assert len(urls) == 2


# ── og:image extraction ──────────────────────────────

def test_extract_og_image_property():
    html = '<html><head><meta property="og:image" content="https://img.example.com/lead.jpg"></head></html>'
    assert extract_og_image(html) == "https://img.example.com/lead.jpg"


def test_extract_og_image_twitter_and_reversed_attrs():
    h1 = '<meta name="twitter:image" content="https://img.example.com/t.png">'
    h2 = '<meta content="https://img.example.com/r.jpg" property="og:image:secure_url">'
    assert extract_og_image(h1) == "https://img.example.com/t.png"
    assert extract_og_image(h2) == "https://img.example.com/r.jpg"


def test_extract_og_image_rejects_http_and_junk():
    assert extract_og_image('<meta property="og:image" content="http://insecure.example.com/a.jpg">') is None
    assert extract_og_image('<meta property="og:image" content="data:image/png;base64,xxx">') is None
    assert extract_og_image("<html>no image</html>") is None
    assert extract_og_image("") is None


# ── Buffer assets payload ────────────────────────────

def test_create_post_with_image_builds_assets():
    fake = {"createPost": {"post": {"id": "p1", "dueAt": "2026-01-01T00:00:00.000Z", "status": "scheduled"}}}
    with patch("app.buffer._gql", return_value=fake) as gql:
        out = create_scheduled_post("ch1", "hello", "2026-01-01T00:00:00.000Z", image_urls=["https://img.example.com/a.jpg"])
    assert out["id"] == "p1"
    q = gql.call_args[0][0]
    assert "assets" in q
    assert "https://img.example.com/a.jpg" in q
    assert "{ image: { url:" in q


def test_create_post_without_image_has_no_assets():
    fake = {"createPost": {"post": {"id": "p2", "dueAt": "2026-01-01T00:00:00.000Z", "status": "scheduled"}}}
    with patch("app.buffer._gql", return_value=fake) as gql:
        create_scheduled_post("ch1", "hello", "2026-01-01T00:00:00.000Z")
    assert "assets" not in gql.call_args[0][0]


def test_create_post_skips_non_https_images():
    fake = {"createPost": {"post": {"id": "p3", "dueAt": "2026-01-01T00:00:00.000Z", "status": "scheduled"}}}
    with patch("app.buffer._gql", return_value=fake) as gql:
        create_scheduled_post("ch1", "hi", "2026-01-01T00:00:00.000Z", image_urls=["http://insecure.example.com/x.jpg"])
    assert "assets" not in gql.call_args[0][0]


def test_create_post_caps_images_per_post():
    fake = {"createPost": {"post": {"id": "p4", "dueAt": "2026-01-01T00:00:00.000Z", "status": "scheduled"}}}
    with patch("app.buffer._gql", return_value=fake) as gql:
        create_scheduled_post("ch1", "hi", "2026-01-01T00:00:00.000Z", image_urls=[
            "https://i.example.com/1.jpg", "https://i.example.com/2.jpg", "https://i.example.com/3.jpg",
        ])
    q = gql.call_args[0][0]
    assert q.count("image: { url:") == config.MAX_IMAGES_PER_POST


# ── Daily image quota ────────────────────────────────

def test_image_quota_counts_and_caps():
    s = StateStore(pathlib.Path(tempfile.mkdtemp()) / "s.json")
    s.load()
    day = "2024-06-01"
    assert s.image_quota_remaining(day) == config.MAX_IMAGE_POSTS_PER_DAY
    for _ in range(3):
        s.increment_daily(day, "image_posts", 1)
    assert s.image_quota_remaining(day) == config.MAX_IMAGE_POSTS_PER_DAY - 3
    s.increment_daily(day, "image_posts", 1)
    assert s.image_quota_remaining(day) == 0  # 4/day budget exhausted


def test_image_quota_rolls_daily():
    s = StateStore(pathlib.Path(tempfile.mkdtemp()) / "s.json")
    s.load()
    s.increment_daily("2024-06-01", "image_posts", 4)
    # next day → full budget again
    assert s.image_quota_remaining("2024-06-02") == config.MAX_IMAGE_POSTS_PER_DAY


def test_image_quota_does_not_block_text_posts():
    # text-only posts increment ai_scheduled, not image_posts — quota unaffected
    s = StateStore(pathlib.Path(tempfile.mkdtemp()) / "s.json")
    s.load()
    day = "2024-06-01"
    s.increment_daily(day, "ai_scheduled", 5)
    assert s.image_quota_remaining(day) == config.MAX_IMAGE_POSTS_PER_DAY
