"""Rights-aware images: curated assets, Wikimedia Commons, and optional article media.

Selection order: curated library → Commons CC0/public domain → opt-in article media.
Rotation is persisted in state so the same photo never repeats back-to-back.
"""
from __future__ import annotations

import json
import pathlib
import re
import requests
import time
from urllib.parse import urlsplit

from app import config
from app.logging_setup import get_logger
from app.normalize import canonical_url

log = get_logger("x-news-bot.images")

INDEX_PATH = pathlib.Path(config.DATA_DIR) / "images" / "index.json"
_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp)$", re.IGNORECASE)
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_SAFE_LICENSES = {"cc0", "cc0 1.0", "public domain", "public domain mark", "public domain mark 1.0", "pd"}


def _eligible_license(value: str) -> bool:
    return str(value).strip().casefold() in _SAFE_LICENSES


def match_entities(title: str) -> list[dict]:
    """Match explicit names in the final copy, not countries or background summary."""
    try:
        registry = json.loads((pathlib.Path(config.DATA_DIR) / "images" / "entities.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    hits = []
    for slug, entity in registry.items():
        positions = [m.start() for alias in entity["aliases"]
                     for m in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", title, re.I)]
        if positions:
            hits.append((0 if entity["type"] == "person" else 1, min(positions), {**entity, "slug": slug}))
    hits.sort(key=lambda hit: hit[:2])
    return [entity for _, _, entity in hits][:2]


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
            if re.search(r"(?<!\w)" + re.escape(kw.strip()) + r"(?!\w)", text, re.I):
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
        and _eligible_license(licenses.get(f, ""))
    ]


def curated_image_metadata(url: str) -> dict | None:
    """Return rights/provenance metadata for a selected curated image URL."""
    if not url:
        return None
    idx = load_index()
    for slug, entry in idx.items():
        for filename in entry.get("files") or []:
            if url == _build_url(slug, filename):
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


def search_commons_image(story: dict, entity: dict | None = None) -> dict | None:
    """Find a Commons image with a public-domain/CC0 license and retain provenance.

    CC-BY and other attribution-required licenses are intentionally excluded: the
    publishing policy forbids source/credit text in the X post, and Buffer's
    current asset interface does not expose a reliable attribution field.
    """
    if not getattr(config, "ENABLE_COMMONS_IMAGES", True):
        return None
    entities = [entity] if entity else match_entities(str(story.get("title", "")))
    if not entities:
        return None
    entity = entities[0]
    query = f'"{entity["name"]}" ' + ("portrait" if entity["type"] == "person" else "building")
    try:
        response = requests.get(
            _COMMONS_API,
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
                "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime",
                "iiurlwidth": 1600,
            },
            headers={"User-Agent": "x-news-bot/1.0 (https://github.com/oKarun-b/x-news-bot)"},
            timeout=(3, 8),
        )
        response.raise_for_status()
        pages = (response.json().get("query") or {}).get("pages") or {}
        for page in sorted(pages.values(), key=lambda page: page.get("index", 999)):
            imageinfo = (page.get("imageinfo") or [{}])[0]
            ext = imageinfo.get("extmetadata") or {}
            file_title = page.get("title", "").replace("_", " ")
            description = re.sub(r"<[^>]+>", " ", (ext.get("ImageDescription") or {}).get("value", ""))
            searchable = file_title + " " + description
            if not any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", file_title, re.I)
                       for alias in [entity["name"]] + entity["aliases"]):
                continue
            if re.search(r"impersonator|caricature|cartoon|waxwork|statue|watermark|logo|montage|collage|AI.generated", searchable, re.I):
                continue
            if entity["type"] == "person" and not re.search(r"\bportrait\b", file_title, re.I):
                continue
            if str((ext.get("AttributionRequired") or {}).get("value", "false")).lower() != "false":
                continue
            license_name = re.sub(r"<[^>]+>", "", (ext.get("LicenseShortName") or {}).get("value", "")).strip()
            if not _eligible_license(license_name):
                continue
            image_url = imageinfo.get("thumburl") or imageinfo.get("url")
            if not image_url or not image_url.startswith("https://"):
                continue
            width = int(imageinfo.get("thumbwidth") or imageinfo.get("width") or 0)
            if width < 800 or imageinfo.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                continue
            source_url = imageinfo.get("descriptionurl", "")
            return {
                "url": canonical_url(image_url),
                "title": page.get("title", ""),
                "license": license_name,
                "creator": re.sub(r"<[^>]+>", "", (ext.get("Artist") or {}).get("value", "")).strip(),
                "source_url": source_url,
                "provider": "Wikimedia Commons",
                "entity": entity["name"],
                "usage": "illustrative portrait" if entity["type"] == "person" else "illustrative building",
            }
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        log.info("Wikimedia Commons image search unavailable: %s", exc)
    return None


def _reachable_image(url: str) -> bool:
    """Bounded preflight of a direct, publicly hosted image; never follow redirects."""
    parsed = urlsplit(url)
    allowed = {"upload.wikimedia.org", "thumb.wikimedia.org", "raw.githubusercontent.com"}
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.port not in (None, 443):
        return False
    try:
        with requests.head(url, headers={"User-Agent": "x-news-bot/1.0 (https://github.com/oKarun-b/x-news-bot)"}, timeout=(3, 8), allow_redirects=False) as response:
            return (response.status_code == 200
                    and response.headers.get("Content-Type", "").split(";")[0] in ("image/jpeg", "image/png", "image/webp")
                    and 0 < int(response.headers.get("Content-Length", 0)) <= 5 * 1024 * 1024)
    except (requests.RequestException, ValueError):
        return False


def select_story_images(story: dict, state: dict) -> list[dict]:
    """Select at most two distinct named-subject images with private provenance."""
    if not config.ENABLE_IMAGES:
        return []
    assets = []
    now = time.time()
    cache = state.setdefault("image_cache", {})
    # Keep persisted cache bounded, including negative lookups.
    for key in list(cache):
        if not isinstance(cache[key], dict) or cache[key].get("expires", 0) <= now:
            del cache[key]
    for key in list(cache)[:-100]:
        del cache[key]
    for entity in match_entities(story.get("title", ""))[:min(2, config.MAX_IMAGES_PER_POST)]:
        slug = entity["slug"]
        asset = None
        files = _files_for(slug)
        if files:
            rotation = state.setdefault("image_rotation", {})
            pick = (rotation.get(slug, -1) + 1) % len(files)
            url = _build_url(slug, files[pick])
            metadata = curated_image_metadata(url)
            if metadata and metadata.get("creator") and metadata.get("source_url"):
                asset = {"url": url, "metadata": {**metadata, "entity": entity["name"], "usage": "illustrative"}}
                rotation[slug] = pick
        if not asset and config.ENABLE_COMMONS_IMAGES:
            if slug in cache:
                asset = cache[slug].get("asset")
            else:
                result = search_commons_image(story, entity)
                asset = {"url": result["url"], "metadata": {k: v for k, v in result.items() if k != "url"}} if result else None
                cache[slug] = {"asset": asset, "expires": now + (86400 if asset else 3600)}
        if asset and asset["url"] not in [a["url"] for a in assets] and _reachable_image(asset["url"]):
            assets.append(asset)
    return assets


def has_known_image(title: str, state: dict) -> bool:
    """Cheap ranking hint, without performing speculative provider requests."""
    for entity in match_entities(title):
        cached = state.get("image_cache", {}).get(entity["slug"], {})
        if _files_for(entity["slug"]) or (cached.get("expires", 0) > time.time() and cached.get("asset")):
            return True
    return False
