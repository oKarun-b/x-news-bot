# x-news-bot

Autonomous X (Twitter) news-posting bot — rolling, freshness-first editorial system.

> **Status:** Phase 1 skeleton — foundation only. Full pipeline lands in later phases.

## What this is

- **GitHub Actions is the only runtime.** No VPS/daemon/self-hosted runner. Each run collects → decides → schedules via Buffer → exits. Buffer handles future publication.
- **Rolling queue** over a 3-hour horizon; normal posts use natural gaps (25–180 min), breaking posts slot near-immediately.
- **Polling every ~10 minutes** during the active window (06:00–23:30 BOT time). Cheap-first gates: nothing new/relevant → 0 OpenRouter + 0 Buffer calls.
- Maximum **10 total / 8 AI posts per day** (configurable; remaining slots reserved for manual/fixed posts).

## Quick start (local)

```bash
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # fill in OPENROUTER_API_KEY, BUFFER_ACCESS_TOKEN, BUFFER_CHANNEL_ID
python -m app.main --dry-run   # full pipeline, no Buffer sends
```

## Configuration

All tuning lives in `.env` (see `.env.example`). Central defaults in `app/config.py`. The feed list (24 feeds) is defined there as `FEEDS` — easy to edit.

Editorial style (formats, urgency scale, attribution, writing rules) is isolated in `app/editorial.py`.

## Secrets

| Secret | Where to set |
|---|---|
| `OPENROUTER_API_KEY` | `.env` locally, GitHub Secrets in CI |
| `BUFFER_ACCESS_TOKEN` | same — from publish.buffer.com/settings/api |
| `BUFFER_CHANNEL_ID` | same — `python -m app.buffer --verify` prints available channels |

Never commit `.env`.

## Project structure

See `app/` — Phase 1 ships: `config`, `logging_setup`, `editorial`, `state.json`/`fixed_posts.json` scaffolds. Remaining modules (news, clustering, ranking, AI, validation, Buffer, scheduler, database) arrive in Phases 2–8.

## GitHub Actions (added in Phase 8)

- Cron `3,13,23,33,43,53 * * * *` (10-min polling) + `workflow_dispatch`
- Per-job timeout ~5 min, `concurrency` group `news-bot` to prevent overlap
- Commits `data/state.json` only when changed

Detailed scheduling/Buffer/reality docs (§39/§40) land with the workflow.
