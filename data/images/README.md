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
- No matching approved asset → the bot searches Wikimedia Commons for CC0/public-domain images. Article images are disabled by default because logos/watermarks cannot be reliably screened automatically.
- Folders/keywords come from `index.json` — add a new entity by copying the pattern.

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
