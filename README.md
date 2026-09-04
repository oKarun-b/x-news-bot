# x-news-bot

Autonomous X (Twitter) news-posting bot — rolling, freshness-first editorial system. GitHub Actions is the **only** runtime; Buffer handles future publication.

## Architecture

```
RSS / Google News
  ↓  10-min polling (06:00-23:30 BOT time)
Collect → Normalize → Dedupe (URL → title) → Cluster → Rank
  ↓  cheap-first gates (no new/relevant story → 0 OpenRouter + 0 Buffer calls)
Editorial AI (select/reject + format) → Generate → Validate (≤260, rewrite loop)
  ↓  two paths
NORMAL (25-180 min gaps, ≤3 queued, ≤180 min horizon)   BREAKING (now+2 min, bypasses queue)
  ↓
Buffer GraphQL (api.buffer.com, customScheduled) → X
  ↓  commit data/state.json (rolling 7-day history)
GitHub Actions exits — Buffer publishes later
```

## Quick start

```bash
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env  # fill OPENROUTER_API_KEY, BUFFER_ACCESS_TOKEN, BUFFER_CHANNEL_ID
python -m app.main --dry-run          # full pipeline, no Buffer sends
python -m app.main --dry-run --force  # bypass active-window gate
python -m app.buffer --verify         # verify Buffer credentials + channel
pytest -q
```

## Secrets

| Secret | Source |
|---|---|
| `OPENROUTER_API_KEY` | openrouter.ai/keys |
| `BUFFER_ACCESS_TOKEN` | publish.buffer.com/settings/api |
| `BUFFER_CHANNEL_ID` | `python -m app.buffer --verify` (or Buffer channel settings) |
| `BUFFER_ORGANIZATION_ID` | auto-discovered; set only to pin it |

Set locally in `.env`, in CI via **GitHub Secrets** (repo Settings → Secrets → Actions). Never commit `.env`.

## Configuration

All tuning is env-driven (`.env.example` documents every key). Central defaults in `app/config.py`.

| Key | Default | Meaning |
|---|---|---|
| `BOT_TIMEZONE` | UTC | All daily counts + windows use this zone |
| `ACTIVE_MONITORING_START/END` | 06:00 / 23:30 | Polling window; outside → overnight collection only or instant exit |
| `POSTING_START/END_HOUR` | 7 / 22 | Scheduler posting window |
| `MAX_TOTAL_POSTS_PER_DAY` | 10 | Hard daily cap |
| `MAX_AI_POSTS_PER_DAY` | 8 | AI posts cap (2 slots reserved for fixed posts) |
| `MAX_BUFFER_AHEAD_POSTS` | 3 | Rolling queue depth (Buffer Free plan ~10 queued/channel) |
| `MAX_SCHEDULE_HORIZON_MINUTES` | 180 | Never schedule normal posts further than this |
| `OPENROUTER_MODEL` | gemini-2.0-flash-exp:free | Configurable; keep temperature 0.4 editorial / 0.7 generation |

Feed list (24 feeds: BBC + Google News searches) is the `FEEDS` registry in `app/config.py` — toggle per-feed via `enabled`.

Writing style is isolated in `app/editorial.py` — the only place for format definitions, urgency scale, attribution rules, and prompts.

## GitHub Actions — schedule reality (§39)

GitHub scheduled workflows are **periodic triggers, not real-time guarantees**. They can be delayed under load. The bot compensates via:

- Frequent 10-min polling (staggered at :03/:13/:23/:33/:43/:53 to avoid top-of-hour contention)
- Every run fetches a recent RSS window and compares against `first_seen_at`/`last_seen_at`
- State in `data/state.json` committed back after each run enables next-run recovery
- Buffer owns future publication — the runner never sleeps

Scheduled workflows on **public repos are free**; 144 runs/day × ~1–2 min would exceed the 2,000 min/month free allowance on private repos. This repo is intended to be **public**.

Schedules are disabled after 60 days of no repo activity — but the bot commits state frequently, so activity is continuous.

## Buffer reality (§40)

- API: **GraphQL** at `https://api.buffer.com`, Bearer token, `createPost(mode: customScheduled, dueAt: ISO8601)` / `posts(status: [scheduled])` / `deletePost`. Old REST (`api.bufferapp.com/1/…`) is deprecated — not used.
- Rate limits on Free: **100 req/day** — the bot queries the Buffer queue **only before scheduling** + periodic reconcile, budgeting ~25–30 req/day.
- Limits: Free plan caps ~10 queued posts/channel and 100 API calls/day. `MAX_BUFFER_AHEAD_POSTS=3` is conservative for Free.
- This project is not responsible for keeping a runner alive. GitHub Actions creates the scheduled Buffer post and exits.

## Daily limits & acceptance scenarios

`MAX_TOTAL=10`, `MAX_AI=8`, 2 reserved for fixed posts (`data/fixed_posts.json` scaffold). Never fills quota with filler — 3 worthwhile stories = 3 posts.

Covered by unit tests + mocked integration (see `tests/`):

1. No new news → 0 AI / 0 Buffer calls
2. One normal story → schedules within horizon
3. Breaking while normals queued → breaking slots near-immediately
4. Same event from 5 sources → 1 cluster, 1 post
5. New development on known story → new post after `MIN_DEVELOPMENT_GAP`
6. 10/day reached → no additional AI posts
7. Queue near capacity → normal deferred, breaking may still proceed
8. Missed/delayed run → next run recovers via RSS window + state
9. OpenRouter failure → retry, no malformed Buffer post
10. Buffer failure → recorded as failed, daily count not incremented, no dup on retry
11. Overlapping runs → `concurrency: news-bot` prevents duplicate scheduling
12. `DRY_RUN=true` → full pipeline, zero Buffer mutations

## Monitoring

Every run logs. The end-of-run **RUN SUMMARY** block reports: feeds attempted/succeeded/failed, articles collected, url/title/history dedup, clusters, candidates, AI calls/failures, posts generated/rejected, daily counts, Buffer queue size, scheduled posts, next due time, errors. Secrets are never logged.

## Manual testing via Actions

Actions → news-bot → **Run workflow** → toggle `dry_run` / `force`.

## License

MIT
