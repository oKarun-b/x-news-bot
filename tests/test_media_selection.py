"""Media provider contracts, relevance filtering and two-subject selection."""
from unittest.mock import MagicMock

import pytest

from app import config, images


def page(name="Donald Trump", license="Public domain", **kwargs):
    return {"title": f"File:{name} official portrait.jpg", "index": 1, "imageinfo": [{
        "thumburl": "https://upload.wikimedia.org/portrait.jpg", "thumbwidth": 1200,
        "mime": "image/jpeg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Portrait.jpg",
        "extmetadata": {"LicenseShortName": {"value": license}, "AttributionRequired": {"value": "false"}},
        **kwargs,
    }]}


def test_names_only_not_country_or_substrings():
    assert [e["slug"] for e in images.match_entities("Trump says he will meet Zelensky")] == ["trump", "zelensky"]
    assert images.match_entities("Trumpets sound in Russia and China") == []
    assert images.match_entities("European Central Bank changes policy")[0]["slug"] == "ecb"


@pytest.mark.parametrize("result", [
    page(name="Donald Trump impersonator"), page(name="Vladimir Putin"),
    page(license="not public domain"), page(license="CC BY-SA 4.0"), page(thumbwidth=300), page(mime="image/svg+xml"),
])
def test_unsuitable_media_rejected(monkeypatch, result):
    response = MagicMock()
    response.json.return_value = {"query": {"pages": {"1": result}}}
    monkeypatch.setattr(images.requests, "get", MagicMock(return_value=response))
    assert images.search_commons_image({"title": "Trump announces talks"}) is None


def test_two_entities_two_images_and_cached_lookups(monkeypatch):
    monkeypatch.setattr(images, "_files_for", lambda slug: [])
    search = MagicMock(side_effect=lambda story, entity: {
        "url": f'https://upload.wikimedia.org/{entity["slug"]}.jpg',
        "license": "Public domain", "entity": entity["name"],
    })
    monkeypatch.setattr(images, "search_commons_image", search)
    monkeypatch.setattr(images, "_reachable_image", lambda url: True)
    state = {}
    story = {"title": "Trump says he plans to meet Zelensky"}
    assets = images.select_story_images(story, state)
    assert len(assets) == 2
    assert assets[0]["url"] != assets[1]["url"]
    assert images.select_story_images(story, state) == assets
    assert search.call_count == 2


def test_bad_media_url_is_not_requested(monkeypatch):
    head = MagicMock()
    monkeypatch.setattr(images.requests, "head", head)
    assert not images._reachable_image("https://localhost/image.jpg")
    assert not images._reachable_image("https://evil.example/image.jpg")
    head.assert_not_called()


def test_preflight_rejects_redirect_html_and_large_file(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 302
    response.headers = {"Content-Type": "image/jpeg", "Content-Length": "10000"}
    monkeypatch.setattr(images.requests, "head", MagicMock(return_value=response))
    url = "https://upload.wikimedia.org/portrait.jpg"
    assert not images._reachable_image(url)
    response.status_code = 200
    response.headers["Content-Type"] = "text/html"
    assert not images._reachable_image(url)
    response.headers = {"Content-Type": "image/jpeg", "Content-Length": str(6 * 1024 * 1024)}
    assert not images._reachable_image(url)
    response.headers["Content-Length"] = "10000"
    assert images._reachable_image(url)
