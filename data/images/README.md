# Curated image library

## Current presentation

- Six reviewed public-domain portraits covering five leaders, stored in this repository.
- Every output is a **1200 × 1500 RGB JPEG (4:5)**, without added headlines, borders or publisher branding.
- One named person → one portrait; two named people → two separate images. An incomplete pair becomes text-only.
- Library portraits are archival illustrations. Their dates are retained; they must not be represented as photographs of a newly reported event.
- [Open the layout preview](preview.html). The browser preview illustrates the pairing; X controls its actual layout/crops.
- The image-bearing-post cap remains four per day. More matching stories can still publish as text.

## Prepare and expand

Install `pip install -r requirements-media.txt`. Add a specific Commons file title to `portraits.json` with `reviewed: false`, then download originals and their rights metadata to a temporary directory:

```powershell
python -m app.media_library --cache "$env:LOCALAPPDATA\Temp\opencode\portraits"
```

Inspect the original contact sheet and source metadata for correct identity, clean framing, rights and absence of overlays. Set normalized crop coordinates `[left, top, right, bottom]` (0–1), mark the entry reviewed, then build:

```powershell
python -m app.media_library --cache "$env:LOCALAPPDATA\Temp\opencode\portraits" --build
```

Review `prepared.jpg` in that cache directory. Build writes the JPEGs and updates `index.json` with dimensions, crop, creator, source, image date and SHA-256. Add the subject's aliases to `entities.json` if needed. Commit/push the resulting assets with the metadata so Buffer can fetch their public URLs. Existing dated files are archival assets; use a new filename for a replacement rather than silently changing its content.

Commons auto-search is **off by default** for a consistent reviewed look. Enable `ENABLE_COMMONS_IMAGES=true` only if metadata-filtered, unreviewed fallback images are acceptable. The preparation command can retrieve Commons files regardless of that runtime setting.

## Manual files

Drop rights-cleared photos into the matching folder and push (or upload via GitHub web UI):

    data/images/trump/trump1.jpg
    data/images/trump/trump2.jpg
    data/images/netanyahu/netanyahu1.jpg
    ...

Rules:
- .jpg / .jpeg / .png / .webp — high resolution (1200px+ preferred)
- The bot rotates through multiple approved files per subject; a subject with one file necessarily reuses it.
- Only CC0 or public-domain assets are eligible for automatic posting. Assets with unknown rights or an attribution-required license are skipped.
- Record each file's license, creator, and original/source URL in `index.json`; these details remain in the internal post ledger.
- No matching approved asset → text-only (or Commons if explicitly enabled). Article/RSS images are not used by the publishing selector.
- Add names and aliases to `entities.json`, and matching folder/metadata entries to `index.json`. Names must appear in the final post; background mentions and country names alone do not trigger portraits.
- Runtime curated selection also requires per-file `assets` metadata with `reviewed: true`, `width: 1200`, and `height: 1500`. The preparation command creates these records.

Nothing else to configure: files are served via raw.githubusercontent.com, which
Buffer accepts as a public/https/stable image URL. Pinterest is not used: Pins do not grant reuse rights and Pinterest has no suitable open image-search API for this workflow.

Add metadata alongside each filename, for example:

```json
{
  "trump": {
    "keywords": ["trump", "donald trump"],
    "priority": 1,
    "files": ["portrait.jpg"],
    "licenses": {"portrait.jpg": "Public domain"},
    "creators": {"portrait.jpg": "Photographer or agency"},
    "source_urls": {"portrait.jpg": "https://commons.wikimedia.org/wiki/File:..."}
  }
}
```
