"""Curated image library — high-quality local photos served via raw.githubusercontent.com.

Selection order in main.py: curated library → og:image → RSS media → text-only.
Rotation is persisted in state so the same photo never repeats back-to-back.
"""
from __future__ import annotations

import json
import pathlib
import re
import requests

from app import config
from app.logging_setup import get_logger

log = get_logger("x-news-bot.images")

INDEX_PATH = pathlib.Path(config.DATA_DIR) / "images" / "index.json"
_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp)$", re.IGNORECASE)
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_SAFE_LICENSES = {"cc0", "public domain", "public domain mark", "pd"}


def load_index() -> dict[str, dict]:
    """Load index.json; entries missing/empty fall back to a no-op."""
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            idx = json.load(f)
        return idx if isinstance(idx, dict) else {}
    except Exception as exc:
        log.info("Image index not available (%s) — curated library disabled", exc)
        return {}


def _story_text(story: dict) -> str:
    return f"{story.get('title', '')} {story.get('summary', '')}".lower()


def match_slugs(story: dict) -> list[str]:
    """Slugs whose keywords appear in the story text, sorted by priority then longest keyword match."""
    idx = load_index()
    text = _story_text(story)
    hits: list[tuple[int, int, str]] = []  # (priority, -match_len, slug)
    for slug, info in idx.items():
        best = 0
        for kw in info.get("keywords", []):
            if kw.lower() in text:
                best = max(best, len(kw))
        if best > 0:
            hits.append((int(info.get("priority", 9)), -best, slug))
    hits.sort()
    return [h[2] for h in hits]


def _build_url(slug: str, file: str) -> str | None:
    if not _IMG_EXT_RE.search(file):
        return None
    file_q = file.replace(" ", "%20")
    return f"{config.IMAGE_BASE_URL.rstrip('/')}/{slug}/{file_q}"


def _files_for(slug: str) -> list[str]:
    idx = load_index()
    entry = idx.get(slug) or {}
    licenses = entry.get("licenses") or {}
    # Publishing without source/credit text is limited to CC0 and public-domain media.
    return [
        f for f in (entry.get("files") or [])
        if isinstance(f, str) and _IMG_EXT_RE.search(f)
        and any(safe in str(licenses.get(f, "")).lower() for safe in _SAFE_LICENSES)
    ]


def curated_image_metadata(url: str) -> dict | None:
    """Return rights/provenance metadata for a selected curated image URL."""
    if not url:
        return None
    idx = load_index()
    for slug, entry in idx.items():
        for filename in entry.get("files") or []:
            if url.endswith("/" + filename.replace(" ", "%20")):
                return {
                    "provider": "curated_library",
                    "slug": slug,
                    "license": (entry.get("licenses") or {}).get(filename, ""),
                    "creator": (entry.get("creators") or {}).get(filename, ""),
                    "source_url": (entry.get("source_urls") or {}).get(filename, ""),
                }
    return None


def select_curated_image(story: dict, state: dict) -> str | None:
    """
    Pick a curated image URL for the story.
    Rotation cursor (state['image_rotation']) guarantees no immediate repeat per slug.
    """
    if not config.ENABLE_IMAGES:
        return None
    idx = load_index()
    if not idx:
        return None
    rotation: dict[str, int] = state.setdefault("image_rotation", {})
    for slug in match_slugs(story):
        files = _files_for(slug)
        if not files:
            continue
        n = len(files)
        start = rotation.get(slug, -1)
        pick = (start + 1) % n
        url = _build_url(slug, files[pick])
        if url:
            rotation[slug] = pick
            log.info("Curated image: %s/%s (rotation %d/%d)", slug, files[pick], pick + 1, n)
            return url
    return None


def save_rotation(state: dict) -> None:
    """No-op hook — rotation dict lives inside state and is persisted with it."""
    return None


def search_commons_image(story: dict) -> dict | None:
    """Find a Commons image with a public-domain/CC0 license and retain provenance.

    CC-BY and other attribution-required licenses are intentionally excluded: the
    publishing policy forbids source/credit text in the X post, and Buffer's
    current asset interface does not expose a reliable attribution field.
    """
    if not getattr(config, "ENABLE_COMMONS_IMAGES", True):
        return None
    title = re.sub(r"[^\w\s-]", " ", str(story.get("title", "")))
    query = " ".join(title.split()[:8]).strip()
    if len(query) < 3:
        return None
    try:
        response = requests.get(
            _COMMONS_API,
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
                "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|extmetadata",
                "iiurlwidth": 1600,
            },
            headers={"User-Agent": "x-news-bot/1.0 (https://github.com/oKarun-b/x-news-bot)"},
            timeout=(3, 8),
        )
        response.raise_for_status()
        pages = (response.json().get("query") or {}).get("pages") or {}
        for page in pages.values():
            imageinfo = (page.get("imageinfo") or [{}])[0]
            ext = imageinfo.get("extmetadata") or {}
            license_name = re.sub(r"<[^>]+>", "", (ext.get("LicenseShortName") or {}).get("value", "")).strip()
            normalized_license = license_name.lower()
            if not any(safe in normalized_license for safe in _SAFE_LICENSES):
                continue
            image_url = imageinfo.get("thumburl") or imageinfo.get("url")
            if not image_url or not image_url.startswith("https://"):
                continue
            width = int(imageinfo.get("thumbwidth") or imageinfo.get("width") or 0)
            if width and width < 800:
                continue
            source_url = imageinfo.get("descriptionurl", "")
            return {
                "url": image_url,
                "title": page.get("title", ""),
                "license": license_name,
                "creator": re.sub(r"<[^>]+>", "", (ext.get("Artist") or {}).get("value", "")).strip(),
                "source_url": source_url,
                "provider": "Wikimedia Commons",
            }
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        log.info("Wikimedia Commons image search unavailable: %s", exc)
    return None


def select_story_image(cluster: dict, story: dict, state: dict, *, allow_article_images: bool = False) -> dict | None:
    """Choose one media asset for a story and return URL plus private provenance."""
    match_story = {
        "title": cluster.get("representative_title") or story.get("title", ""),
        "summary": story.get("summary", ""),
    }
    curated_url = select_curated_image(match_story, state)
    if curated_url:
        return {"url": curated_url, "metadata": curated_image_metadata(curated_url)}

    commons_asset = search_commons_image(match_story)
    if commons_asset:
        return {
            "url": commons_asset["url"],
            "metadata": {key: value for key, value in commons_asset.items() if key != "url"},
        }

    if allow_article_images:
        og_image = cluster.get("og_image")
        if isinstance(og_image, str) and og_image.startswith("https://"):
            return {"url": og_image, "metadata": {"provider": "article_metadata"}}
        representative = cluster.get("representative_article") or {}
        for article in [representative] + (cluster.get("articles") or []):
            if not isinstance(article, dict):
                continue
            for url in article.get("image_urls") or []:
                if isinstance(url, str) and url.startswith("https://"):
                    return {"url": url, "metadata": {"provider": "article_rss"}}
    return None
