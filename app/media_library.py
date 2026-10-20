"""Offline portrait preparation: python -m app.media_library --help.

Run only while maintaining the library, never on the publishing hot path.
Originals and metadata are cached outside the repository for visual review.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests

from app.images import _eligible_license
from app.normalize import canonical_url

HEADERS = {"User-Agent": "x-news-bot/1.0 (https://github.com/oKarun-b/x-news-bot)"}
SIZE = (1200, 1500)


def plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def download(entry: dict, cache: Path) -> tuple[Path, dict]:
    key = hashlib.sha256(entry["commons_title"].encode()).hexdigest()[:20]
    image_path, metadata_path = cache / f"{key}.jpg", cache / f"{key}.json"
    if image_path.exists() and metadata_path.exists():
        return image_path, json.loads(metadata_path.read_text(encoding="utf-8"))
    response = requests.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "titles": entry["commons_title"],
        "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 2000,
    }, headers=HEADERS, timeout=(5, 20))
    response.raise_for_status()
    page = next(iter(response.json()["query"]["pages"].values()))
    info = page["imageinfo"][0]
    ext = info["extmetadata"]
    license_name = plain(ext.get("LicenseShortName", {}).get("value", ""))
    if not _eligible_license(license_name) or ext.get("AttributionRequired", {}).get("value", "true").lower() != "false":
        raise ValueError(f"Asset requires attribution or has unknown rights: {entry['commons_title']}")
    url = canonical_url(info.get("thumburl") or info["url"])
    if urlsplit(url).hostname not in {"upload.wikimedia.org", "thumb.wikimedia.org"}:
        raise ValueError("Unexpected Commons image host")
    metadata = {
        "license": license_name, "creator": plain(ext.get("Artist", {}).get("value", "")),
        "source_url": info["descriptionurl"], "download_url": url,
        "captured_at": plain(ext.get("DateTimeOriginal", {}).get("value", "")),
        "original_source": ext.get("Credit", {}).get("value", ""),
        "description": plain(ext.get("ImageDescription", {}).get("value", ""))[:500],
    }
    if not metadata["creator"]:
        raise ValueError("Missing creator metadata")
    cache.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=HEADERS, timeout=(5, 30), stream=True, allow_redirects=False) as media:
        if media.status_code != 200:
            raise ValueError(f"Image unavailable: HTTP {media.status_code}")
        chunks, size = [], 0
        for chunk in media.iter_content(65536):
            size += len(chunk)
            if size > 20 * 1024 * 1024:
                raise ValueError("Image exceeds preparation size limit")
            chunks.append(chunk)
    image_path.write_bytes(b"".join(chunks))
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return image_path, metadata


def crop_portrait(image, crop: list[float]):
    from PIL import Image, ImageOps
    if len(crop) != 4 or not (0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1):
        raise ValueError("Crop must be a normalized left/top/right/bottom rectangle")
    image = ImageOps.exif_transpose(image).convert("RGB")
    box = tuple(round(v * image.size[i % 2]) for i, v in enumerate(crop))
    image = image.crop(box)
    if image.width < 600 or image.height < 750:
        raise ValueError("Selected portrait crop is too small")
    return ImageOps.fit(image, SIZE, method=Image.Resampling.LANCZOS, centering=(0.5, 0.35))


def main() -> None:
    from PIL import Image, ImageDraw, ImageOps
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/images/portraits.json"))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--build", action="store_true", help="Build only visually reviewed entries into the library")
    parser.add_argument("--output", type=Path, default=Path("data/images"))
    args = parser.parse_args()
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    index_path = args.output / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    sheet = Image.new("RGB", (320 * len(entries), 440), "#151515")
    draw = ImageDraw.Draw(sheet)
    for n, entry in enumerate(entries):
        original, metadata = download(entry, args.cache)
        with Image.open(original) as image:
            if args.build:
                if not entry.get("reviewed"):
                    raise ValueError(f"Visual review required: {entry['entity']}")
                rendered = crop_portrait(image, entry["crop"])
                folder = args.output / entry["entity"]
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / entry["file"]
                rendered.save(target, "JPEG", quality=90, optimize=True, progressive=True)
                info = index.setdefault(entry["entity"], {"type": "person", "priority": 1})
                if entry["file"] not in info.setdefault("files", []):
                    info["files"].append(entry["file"])
                for field, key in (("licenses", "license"), ("creators", "creator"), ("source_urls", "source_url")):
                    info.setdefault(field, {})[entry["file"]] = metadata[key]
                info.setdefault("assets", {})[entry["file"]] = {
                    **{**metadata, "description": metadata.get("description", "")[:500]},
                    "width": SIZE[0], "height": SIZE[1], "crop": entry["crop"],
                    "usage": "illustrative portrait", "reviewed": True,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
                preview = rendered
            else:
                preview = image.copy()
            preview = ImageOps.contain(preview, (310, 400))
            sheet.paste(preview, (n * 320 + (320 - preview.width) // 2, 25))
        draw.text((n * 320 + 8, 6), entry["entity"], fill="white")
        print(entry["entity"], metadata["license"], original)
    sheet.save(args.cache / ("prepared.jpg" if args.build else "originals.jpg"))
    if args.build:
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
