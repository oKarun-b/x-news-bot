"""Curated image library — high-quality local photos served via raw.githubusercontent.com.

Selection order in main.py: curated library → og:image → RSS media → text-only.
Rotation is persisted in state so the same photo never repeats back-to-back.
"""
from __future__ import annotations

import json
import pathlib
import re

from app import config
from app.logging_setup import get_logger

log = get_logger("x-news-bot.images")

INDEX_PATH = pathlib.Path(config.DATA_DIR) / "images" / "index.json"
_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp)$", re.IGNORECASE)


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
    files = (idx.get(slug) or {}).get("files") or []
    return [f for f in files if _IMG_EXT_RE.search(f)]


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
