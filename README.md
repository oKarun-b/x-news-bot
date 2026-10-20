# x-news-bot

Autonomous X (Twitter) news-posting bot — rolling, freshness-first editorial system. GitHub Actions is the **only** runtime; Buffer handles future publication.

## Architecture

```
RSS / Google News
  ↓  12-min discovery cadence (06:00-23:30 BOT time)
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
| `DAILY_POST_TARGET` | 10 | Desired daily total; quality and freshness take priority over filling quota |
| `MAX_TOTAL_POSTS_PER_DAY` | 10 | Absolute daily cap across all post types |
| `MAX_AI_POSTS_PER_DAY` | 10 | Maximum news posts the bot can schedule per day |
| `MAX_NEW_POSTS_PER_RUN` | 2 | Limits each 12-minute discovery run |
| `MAX_BUFFER_AHEAD_POSTS` | 3 | Rolling queue depth (Buffer Free plan ~10 queued/channel) |
| `MAX_SCHEDULE_HORIZON_MINUTES` | 180 | Never schedule normal posts further than this |
| `OPENROUTER_MODEL` | gemini-2.0-flash-exp:free | Configurable; keep temperature 0.4 editorial / 0.7 generation |
| `FEED_FETCH_WORKERS` | 8 | Maximum parallel RSS fetches |
| `ENABLE_COMMONS_IMAGES` | true | Search Wikimedia Commons for CC0/public-domain images |
| `ENABLE_ARTICLE_IMAGES` | false | Collect article image metadata only; publishing uses approved subject images |
| `MAX_IMAGES_PER_POST` | 2 | Up to two separate, relevant subject images |
| `MAX_IMAGE_POSTS_PER_DAY` | 4 | Upper bound for image-bearing posts; missing/unsuitable images fall back to text |

Feed list (24 feeds: BBC + Google News searches) is the `FEEDS` registry in `app/config.py` — toggle per-feed via `enabled`.

Writing prompts and formats live in `app/editorial.py`; deterministic labels, freshness and duplicate checks live in `app/content.py`.

Posts use sentence case and one development: `JUST IN: [flags] [subject] [action or attributed statement] [essential detail]`. The label becomes `UPDATE:` after the fresh-news window, or `CONTEXT:` for explanations. Normally use zero to two flags; three require three explicitly supported participants. Speaker attribution and uncertainty remain; publisher credits, photo captions and undated/stale items are rejected. Failed generation leaves the story eligible for a later run instead of publishing the raw headline.

Public-post policy is enforced twice: generated copy is validated in `app/validate.py`, and Buffer independently blocks links/source references at the API boundary. Research sources and asset license/provenance remain in internal state only. Source handles are excluded from AI mention suggestions.

Images match named subjects in the final copy using `data/images/entities.json`: curated CC0/public-domain portraits → entity-specific Wikimedia Commons search → text-only fallback. Up to two distinct portraits are sent as separate assets, not a collage. They are illustrative images, not evidence of the reported event. Matching and license checks use provider metadata; they do not constitute visual verification. Direct URLs are checked for reachability, image type and size. Successful and empty searches are cached. Add approved images and per-file license/creator/source metadata under `data/images/`; disable Commons with `ENABLE_COMMONS_IMAGES=false`. Publisher images and Pinterest are not used by the publishing selector.

`app/publishing.py` rechecks spacing, publication-time freshness, labels and destination-day quotas immediately before sending. Breaking stories can occupy an earlier free slot instead of being forced behind routine posts. Deferred candidates remain eligible until Buffer accepts them. Dry-run logs show complete copy and selected image URLs without creating Buffer posts.

Feed polling uses a bounded thread pool (`FEED_FETCH_WORKERS`, default 8) and persists ETag/Last-Modified validators in state to reduce repeated downloads. The app already keeps major concerns in separate modules; `app/main.py` remains the orchestrator, with provider and persistence logic isolated in their respective modules.

## GitHub Actions — schedule reality (§39)

GitHub scheduled workflows are **periodic triggers, not real-time guarantees**. They can be delayed under load. The bot compensates via:

- State-driven 12-minute discovery cadence, with cron fallback at :03 and :33
- Every run fetches a recent RSS window and compares against `first_seen_at`/`last_seen_at`
- State in `data/state.json` committed back after each run enables next-run recovery
- Buffer owns future publication — the runner never sleeps
- The workflow dispatches the next chained run after saving state; `GITHUB_TOKEN` is exposed only to the dispatch and state-push steps, not the bot process. Set the repository Actions variable `ENABLE_CHAIN=false` to pause chaining.

Scheduled workflows on **public repos are free**; 144 runs/day × ~1–2 min would exceed the 2,000 min/month free allowance on private repos. This repo is intended to be **public**.

Schedules are disabled after 60 days of no repo activity — but the bot commits state frequently, so activity is continuous.

## Buffer reality (§40)

- API: **GraphQL** at `https://api.buffer.com`, Bearer token, `createPost(mode: customScheduled, dueAt: ISO8601)` / `posts(status: [scheduled])` / `deletePost`. Old REST (`api.bufferapp.com/1/…`) is deprecated — not used.
- Rate limits on Free: **100 req/day** — the bot queries the Buffer queue **only before scheduling** + periodic reconcile, budgeting ~25–30 req/day.
- Limits: Free plan caps ~10 queued posts/channel and 100 API calls/day. `MAX_BUFFER_AHEAD_POSTS=3` is conservative for Free.
- This project is not responsible for keeping a runner alive. GitHub Actions creates the scheduled Buffer post and exits.

## Daily limits & acceptance scenarios

The bot can schedule **up to 10 news posts per day**, with at most 2 per discovery run and a 3-post rolling Buffer queue. Ten is a cap/target, not a promise: it will not fill slots with weak or stale stories. Fixed-post scheduling is not active, so no daily slots are reserved for it.

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
10. Explicit Buffer image rejection → text-only retry; daily image count increments only for accepted media
11. Overlapping runs → `concurrency: news-bot` prevents duplicate scheduling
12. `DRY_RUN=true` → full pipeline, zero Buffer mutations

## Monitoring

Every run logs. The end-of-run **RUN SUMMARY** block reports: feeds attempted/succeeded/failed, articles collected, url/title/history dedup, clusters, candidates, AI calls/failures, posts generated/rejected, daily counts, Buffer queue size, scheduled posts, next due time, errors. Secrets are never logged.

## Manual testing via Actions

Actions → news-bot → **Run workflow** → toggle `dry_run` / `force`.

## License

MIT
