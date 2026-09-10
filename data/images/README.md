# Curated image library

Drop photos into the matching folder and push (or upload via GitHub web UI):

    data/images/trump/trump1.jpg
    data/images/trump/trump2.jpg
    data/images/netanyahu/netanyahu1.jpg
    ...

Rules:
- .jpg / .jpeg / .png / .webp — high resolution (1200px+ preferred)
- The bot rotates through the files in each folder (no immediate repeats)
- No folder/files → that entity is skipped (bot falls back to article images / text-only)
- Folders/keywords come from index.json — add a new entity by copying the pattern

Nothing else to configure: files are served via raw.githubusercontent.com, which
Buffer accepts as a public/https/stable image URL.
