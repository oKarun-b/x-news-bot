"""Reviewable assets, stable image format and real selector integration."""
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image
import pytest

from app import config, images
from app.media_library import crop_portrait, frame_flag


LIBRARY = Path(__file__).resolve().parents[1] / "data" / "images"


@pytest.fixture
def library(monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", LIBRARY.parent)
    monkeypatch.setattr(config, "ENABLE_IMAGES", True)
    monkeypatch.setattr(config, "ENABLE_COMMONS_IMAGES", False)
    monkeypatch.setattr(config, "MAX_IMAGES_PER_POST", 2)
    monkeypatch.setattr(images, "_reachable_image", lambda url: True)


@pytest.mark.parametrize("manifest_name", ["portraits.json", "people-expanded.json", "flags.json"])
def test_shipped_portraits_match_manifest_and_checksums(library, manifest_name):
    manifest = json.loads((LIBRARY / manifest_name).read_text(encoding="utf-8"))
    index = images.load_index()
    for entry in manifest:
        filename = LIBRARY / entry["entity"] / entry["file"]
        metadata = index[entry["entity"]]["assets"][entry["file"]]
        assert entry["reviewed"] and metadata["reviewed"]
        assert metadata["sha256"] == hashlib.sha256(filename.read_bytes()).hexdigest()
        assert filename.stat().st_size < 5 * 1024 * 1024
        assert images._eligible_license(metadata["license"])
        assert metadata["creator"]
        if entry.get("type") == "country":
            assert metadata["layout"] == "contain" and metadata["usage"] == "symbolic country flag"
            assert metadata["country_code"] == entry["country_code"]
        else:
            assert metadata["captured_at"]
        assert metadata["source_url"].startswith("https://commons.wikimedia.org/wiki/File:")
        with Image.open(filename) as image:
            assert image.size == (1200, 1500)
            assert image.mode == "RGB" and image.format == "JPEG"


def test_real_library_supplies_both_portraits_without_search(library, monkeypatch):
    search = MagicMock()
    monkeypatch.setattr(images, "search_commons_image", search)
    assets = images.select_story_images({"title": "Trump says talks with Zelensky may resume"}, {})
    assert [a["metadata"]["entity"] for a in assets] == ["Donald Trump", "Volodymyr Zelensky"]
    assert all(a["metadata"]["reviewed"] for a in assets)
    assert all(a["url"].startswith(config.IMAGE_BASE_URL) for a in assets)
    search.assert_not_called()


def test_incomplete_pair_falls_back_to_text_without_advancing_rotation(library, monkeypatch):
    monkeypatch.setattr(images, "_reachable_image", lambda url: "/zelensky/" not in url)
    state = {}
    assert images.select_story_images({"title": "Trump meets Zelensky"}, state) == []
    assert not state.get("image_rotation")


def test_library_rotates_and_tries_next_reachable_portrait(library, monkeypatch):
    state = {}
    first = images.select_story_images({"title": "Netanyahu announces talks"}, state)
    second = images.select_story_images({"title": "Netanyahu announces talks"}, state)
    assert first[0]["url"] != second[0]["url"]
    monkeypatch.setattr(images, "_reachable_image", lambda url: "portrait-2018" in url)
    assert images.select_story_images({"title": "Netanyahu announces talks"}, {})[0]["url"].endswith("portrait-2018.jpg")


def test_institution_in_background_does_not_get_second_portrait(library):
    assert [e["slug"] for e in images.match_entities("Trump speaks at the White House")] == ["trump"]


def test_unreviewed_curated_image_not_selected(library, monkeypatch):
    monkeypatch.setattr(images, "curated_image_metadata", lambda url: {
        "creator": "Someone", "source_url": "https://example.com", "width": 1200, "height": 1500,
    })
    assert images.select_story_images({"title": "Trump announces talks"}, {}) == []


def test_crop_preserves_portrait_shape_and_rejects_tiny_sources():
    image = Image.new("RGB", (1600, 2000))
    assert crop_portrait(image, [0, 0, 1, 1]).size == (1200, 1500)
    with pytest.raises(ValueError, match="too small"):
        crop_portrait(image, [0, 0, .1, .1])
    with pytest.raises(ValueError, match="normalized"):
        crop_portrait(image, [0, 0, 2, 1])


def test_flag_framing_preserves_all_edges_and_aspect_ratio():
    from PIL import ImageDraw
    image = Image.new("RGB", (1000, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 30, 499), fill="red")
    draw.rectangle((969, 0, 999, 499), fill="blue")
    output = frame_flag(image)
    assert output.size == (1200, 1500)
    # 1120 x 560 foreground, centered; left/right design edges survive intact.
    assert output.getpixel((50, 750)) == (255, 0, 0)
    assert output.getpixel((1150, 750)) == (0, 0, 255)
    assert output.getpixel((600, 475)) == (255, 255, 255)
    assert output.getpixel((600, 465)) == (16, 24, 32)


@pytest.mark.parametrize("title,codes,expected", [
    ("France announces a new energy policy", ["FR"], ["flag-fr"]),
    ("US and Israel announce trade talks", ["US", "IL"], ["flag-us", "flag-il"]),
    ("Ukraine and Russia exchange prisoners", ["UA", "RU"], ["flag-ua", "flag-ru"]),
    ("UK announces a new trade policy", ["UK"], ["flag-gb"]),
    ("EU announces new trade rules", ["EU"], ["flag-eu"]),
    ("France changes policy following US criticism", ["FR"], ["flag-fr"]),
    ("France announces new policy", [], []),
    ("France announces new policy", ["US"], []),
    ("China, Russia and India begin talks", ["CN", "RU", "IN"], []),
    ("This affects us all", ["US"], []),
    ("A product called RUSSIANWARE launches", ["RU"], []),
    ("Musk comments on US policy", ["US"], ["elon-musk"]),
    ("Macron speaks in France", ["FR"], ["macron"]),
    ("Giorgia Meloni meets Narendra Modi", ["IT", "IN"], ["meloni", "modi"]),
])
def test_central_country_flags_and_named_subject_priority(library, title, codes, expected):
    assets = images.select_story_images({"title": title, "country_codes": codes}, {})
    assert [a["metadata"]["slug"] for a in assets] == expected


def test_unavailable_second_flag_is_not_replaced_or_omitted(library, monkeypatch):
    monkeypatch.setattr(images, "_reachable_image", lambda url: "/flag-il/" not in url)
    assert images.select_story_images({"title": "US and Israel hold talks", "country_codes": ["US", "IL"]}, {}) == []


def test_two_flags_reach_buffer_as_distinct_assets(library, monkeypatch):
    from app.buffer import create_scheduled_post
    assets = images.select_story_images({"title": "US and Israel announce talks", "country_codes": ["US", "IL"]}, {})
    gql = MagicMock(return_value={"createPost": {"post": {"id": "paired-flags"}}})
    monkeypatch.setattr("app.buffer._gql", gql)
    result = create_scheduled_post("ch", "JUST IN: US and Israel announce talks.", "2026-10-21T12:00:00Z", [a["url"] for a in assets])
    query = gql.call_args.args[0]
    assert result["id"] == "paired-flags"
    assert query.count("image: { url:") == 2
    assert "/flag-us/flag.jpg" in query and "/flag-il/flag.jpg" in query


def test_eu_flag_rendering_supported():
    from app.countries import codes_to_flags
    assert codes_to_flags(["EU"]) == "🇪🇺"


@pytest.mark.parametrize("url", ["https://ichef.bbci.co.uk/news/photo.jpg", "https://i.pinimg.com/photo.jpg",
                                 "https://raw.githubusercontent.com/other/repo/main/photo.jpg"])
def test_publisher_and_pinterest_media_blocked_at_buffer_boundary(url, monkeypatch):
    from app.buffer import create_scheduled_post
    gql = MagicMock()
    monkeypatch.setattr("app.buffer._gql", gql)
    with pytest.raises(ValueError, match="approved"):
        create_scheduled_post("ch", "JUST IN: A new development.", "2026-10-20T12:00:00Z", image_urls=[url])
    gql.assert_not_called()
