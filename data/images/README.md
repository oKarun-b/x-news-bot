# Curated image library

Drop rights-cleared photos into the matching folder and push (or upload via GitHub web UI):

    data/images/trump/trump1.jpg
    data/images/trump/trump2.jpg
    data/images/netanyahu/netanyahu1.jpg
    ...

Rules:
- .jpg / .jpeg / .png / .webp — high resolution (1200px+ preferred)
- The bot rotates through the files in each folder (no immediate repeats)
- Only CC0 or public-domain assets are eligible for automatic posting. Assets with unknown rights or an attribution-required license are skipped.
- Record each file's license, creator, and original/source URL in `index.json`; these details remain in the internal post ledger.
- No matching approved asset → entity-specific Commons search, then text-only fallback. Article/RSS images are not used by the publishing selector.
- Add names and aliases to `entities.json`, and matching folder/metadata entries to `index.json`. Names must appear in the final post; background mentions and country names alone do not trigger portraits.
- Up to two distinct named subjects can receive separate images. These are illustrative portraits/buildings, not photographs of the current event. Curate for correct identity, neutral presentation and absence of branding; automated provider metadata checks cannot verify every visual detail.

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
