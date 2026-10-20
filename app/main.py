"""Orchestrator — collect → decide → schedule → exit. Never sleeps."""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta, timezone

from app import config
from app.content import central_country_codes, clean_headline, duplicate_development, publication_time, render_post, story_problem
from app.article import enrich_top_candidates
from app.clustering import cluster_articles
from app.database import StateStore, _today_key
from app.filters import dedupe_against_history, dedupe_by_title, dedupe_by_url
from app.logging_setup import get_logger, run_summary
from app.news import collect_articles
from app.rank import rank_clusters
from app.scheduler import compute_schedule, format_due_at, is_within_active_window

log = get_logger("x-news-bot.main")

# Counters for observability
OPENROUTER_CALLS = 0
OPENROUTER_FAILURES = 0
BUFFER_CALLS = 0
BUFFER_FAILURES = 0


def _parse_args():
    p = argparse.ArgumentParser(description="x-news-bot")
    p.add_argument("--dry-run", action="store_true", help="force dry-run (no Buffer sends)")
    p.add_argument("--force", action="store_true", help="bypass active-window gate")
    p.add_argument("--chained", action="store_true", help="invoked by the chain dispatcher")
    return p.parse_args()


def _is_dry_run(cli_dry: bool) -> bool:
    if cli_dry:
        return True
    return config.EFFECTIVE_DRY_RUN


def _wait_for_discovery_slot(store: StateStore, now: datetime) -> float:
    """§23 state-driven cadence: wait until DISCOVERY_INTERVAL since last discovery."""
    last = store.data.get("last_discovery_time")
    if not last:
        return 0.0
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = (now - last_dt).total_seconds() / 60
        wait_min = config.DISCOVERY_INTERVAL_MINUTES - elapsed
        if wait_min <= 0:
            return 0.0
        wait_min = min(wait_min, config.CHAIN_WAIT_MAX_MINUTES)
        log.info("Chain: last discovery %.1f min ago — waiting %.1f min for next slot", elapsed, wait_min)
        import time as _t
        _t.sleep(wait_min * 60)
        return wait_min
    except Exception:
        return 0.0


def _check_yesterday_quota(store: StateStore, now: datetime) -> None:
    """§3/§25: log DAILY_QUOTA_UNFILLED if yesterday missed the minimum."""
    try:
        yesterday = (_today_key(now - timedelta(days=1)))
        y_counts = store.daily_counts_for(yesterday)
        if 0 < y_counts["total"] < config.DAILY_POST_MINIMUM:
            log.warning(
                "DAILY_QUOTA_UNFILLED: %s finished with %d/%d posts (shortfall %d)",
                yesterday, y_counts["total"], config.DAILY_POST_MINIMUM,
                config.DAILY_POST_MINIMUM - y_counts["total"],
            )
    except Exception:
        pass


def _today_counts(store: StateStore, now: datetime) -> dict:
    return store.daily_counts_for(_today_key(now))


def _get_buffer_context(store: StateStore) -> dict | None:
    """Return cached or freshly verified Buffer channel info. Returns None on failure (logs it)."""
    cached = store.data.get("buffer_cache", {})
    if cached.get("organization_id") and cached.get("channel_id"):
        return cached
    # Need to verify — only when we actually need Buffer (lazy, to preserve rate limit)
    try:
        from app.buffer import verify_channel
        global BUFFER_CALLS
        BUFFER_CALLS += 1
        info = verify_channel()
        store.data["buffer_cache"] = {
            "organization_id": info["organization_id"],
            "channel_id": info["channel_id"],
            "channel_name": info.get("channel_name", ""),
            "service": info.get("service", ""),
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        return store.data["buffer_cache"]
    except Exception as exc:
        log.warning("Buffer verify failed: %s", exc)
        return None


def _fetch_buffer_queue(store: StateStore) -> list[dict]:
    ctx = _get_buffer_context(store)
    if not ctx:
        return []
    try:
        from app.buffer import get_scheduled_posts
        global BUFFER_CALLS
        BUFFER_CALLS += 1
        posts = get_scheduled_posts(ctx["organization_id"], ctx["channel_id"], limit=20)
        # Normalize to scheduler format
        ctx["queue_snapshot_at"] = datetime.now(timezone.utc).isoformat()
        return posts
    except Exception as exc:
        global BUFFER_FAILURES
        BUFFER_FAILURES += 1
        log.warning("Buffer queue fetch failed (using local ledger only): %s", exc)
        # Fallback: reconstruct queue from local state posts that are still scheduled & in future
        local = []
        for p in store.data.get("posts", []):
            if p.get("status") == "scheduled":
                try:
                    due = datetime.fromisoformat(p.get("scheduled_at", "").replace("Z", "+00:00"))
                    if due.tzinfo and due > datetime.now(timezone.utc):
                        local.append({"dueAt": p["scheduled_at"], "id": p.get("buffer_post_id", ""), "text": p.get("text", "")[:40]})
                except Exception:
                    continue
        log.info("Local ledger queue size: %d", len(local))
        return local


def run(dry_run_cli: bool = False, force: bool = False, chained: bool = False) -> int:
    global OPENROUTER_CALLS, OPENROUTER_FAILURES, BUFFER_CALLS, BUFFER_FAILURES
    OPENROUTER_CALLS = OPENROUTER_FAILURES = BUFFER_CALLS = BUFFER_FAILURES = 0
    store = StateStore()
    store.load()
    now = datetime.now(timezone.utc)

    # §13: restore OpenRouter daily rate-limit memory (skip dead models across chained runs)
    try:
        from app.ai import set_rate_limited_models, get_rate_limited_models
        ai_state = store.data.get("ai_state") or {}
        if ai_state.get("date") == now.strftime("%Y-%m-%d"):
            set_rate_limited_models(ai_state.get("rate_limited", {}))
    except Exception:
        pass

    # §23: state-driven cadence — chained runs wait for the discovery slot
    if chained:
        _wait_for_discovery_slot(store, now)
        now = datetime.now(timezone.utc)

    # Persist the cadence marker before any early-return path. Otherwise an
    # empty-feed/overnight run immediately dispatches another full run.
    store.set_last_discovery(now)
    store.save()

    dry_run = _is_dry_run(dry_run_cli)

    _check_yesterday_quota(store, now)

    metrics: dict = {
        "run_at": now.isoformat(),
        "dry_run": dry_run,
        "forced": force,
        "current_date": _today_key(now),
    }

    # ── Window gate ──────────────────────────────────
    if not force and not is_within_active_window(now):
        if config.OVERNIGHT_COLLECTION:
            log.info("Outside active window — overnight collection only (no AI/Buffer)")
            # Still collect for history, but skip AI/Buffer below
            overnight_only = True
        else:
            log.info("Outside active window, exiting")
            store.set_last_run({"at": now.isoformat(), "mode": "inactive_skip", "result": "outside_window"})
            store.save()
            run_summary({"result": "outside_window", "dry_run": dry_run})
            return 0
    else:
        overnight_only = False

    # ── Daily capacity pre-check (§3/§5 quota engine) ─
    day_key = _today_key(now)
    counts = _today_counts(store, now)
    metrics["daily_ai"] = counts["ai_scheduled"]
    metrics["daily_total"] = counts["total"]
    metrics["daily_target"] = config.DAILY_POST_TARGET
    metrics["daily_hard_max"] = config.DAILY_POST_HARD_MAX
    remaining = store.remaining_capacity(day_key)
    metrics["ai_remaining"] = remaining["ai_remaining"]
    metrics["total_remaining"] = remaining["total_remaining"]
    metrics["target_remaining"] = remaining["target_remaining"]
    metrics["hard_max_remaining"] = remaining["hard_max_remaining"]

    # §12/§25: quota-aware mode
    behind_target = remaining["target_remaining"] > 0
    catch_up = behind_target and remaining["target_remaining"] >= 4
    metrics["quota_status"] = "BEHIND_TARGET" if behind_target else "TARGET_REACHED"
    if catch_up:
        metrics["quota_status"] = "CATCH_UP"
    log.info(
        "Quota: %d/%d scheduled today, remaining=%d, mode=%s",
        counts["total"], config.DAILY_POST_TARGET, remaining["target_remaining"], metrics["quota_status"],
    )
    # The daily hard cap is absolute; urgent stories bypass queue/window gates, not this cap.
    if remaining["hard_max_remaining"] <= 0:
        log.info("DAILY_POST_HARD_MAX reached (%d) — no more posts today", config.DAILY_POST_HARD_MAX)
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "hard_max_reached"})
        store.save()
        run_summary({**metrics, "result": "hard_max_reached", "ai_calls": 0})
        return 0

    # Per-run budgets scale with quota pressure (§13)
    ai_call_budget = config.MAX_AI_CALLS_PER_RUN_CATCHUP if catch_up else config.MAX_AI_CALLS_PER_RUN
    ai_daily_remaining = max(0, config.MAX_AI_CALLS_PER_DAY - counts.get("ai_calls", 0))
    ai_call_budget = min(ai_call_budget, ai_daily_remaining)
    if ai_call_budget <= 0:
        log.info("MAX_AI_CALLS_PER_DAY reached (%d) — no AI this run", config.MAX_AI_CALLS_PER_DAY)
    top_n = config.TOP_CANDIDATES_CATCHUP if catch_up else config.TOP_CANDIDATES_PER_RUN
    score_floor = config.MIN_CANDIDATE_SCORE_CATCHUP if catch_up else config.MIN_CANDIDATE_SCORE
    metrics["ai_call_budget"] = ai_call_budget
    metrics["catch_up"] = catch_up

    def _record_ai_call() -> None:
        """Count one logical OpenRouter operation against run and daily budgets."""
        global OPENROUTER_CALLS
        OPENROUTER_CALLS += 1
        store.increment_daily(day_key, "ai_calls", 1)

    # ── Collect ──────────────────────────────────────
    # Snapshot history sets BEFORE collect for dedupe_against_history
    seen_canonical = store.seen_canonical_set()
    seen_hashes = store.seen_title_hashes()
    articles, feed_stats = collect_articles(feed_cache=store.data.setdefault("feed_cache", {}))
    metrics["feeds_attempted"] = feed_stats["attempted"]
    metrics["feeds_succeeded"] = feed_stats["succeeded"]
    metrics["feeds_failed"] = feed_stats["failed"]
    metrics["feeds_not_modified"] = feed_stats.get("not_modified", 0)
    metrics["articles_collected"] = len(articles)

    if feed_stats["failed"] > 0:
        # Still continue — one broken feed doesn't kill run
        log.warning("Feeds failed this run: %s", feed_stats["failed_details"])

    if not articles and not (behind_target and store.get_candidate_pool(max_age_hours=config.NEWS_MAX_AGE_HOURS)):
        log.info("No articles collected, exiting")
        store.set_last_run({"at": now.isoformat(), "mode": "collect_only", "result": "no_articles"})
        store.set_last_feed_check(now)
        store.save()
        run_summary({**metrics, "result": "no_articles"})
        return 0

    # ── Dedupe ───────────────────────────────────────
    articles = [a for a in articles if not story_problem(a.get("title", ""), a.get("published_at"), now)]
    articles, url_dups = dedupe_by_url(articles)
    metrics["url_dups"] = url_dups
    articles, title_dups = dedupe_by_title(articles)
    metrics["title_dups"] = title_dups
    # History-aware dedupe — only keep genuinely new articles for clustering
    new_articles, history_dups = dedupe_against_history(articles, seen_canonical, seen_hashes)
    metrics["history_dups"] = history_dups
    metrics["new_articles"] = len(new_articles)
    # Cap new articles to most recent 200 to keep clustering fast (prevents 400+ article slow runs)
    if len(new_articles) > 200:
        # Sort by published_at if available, newest first
        def _sort_key(a):
            pa = a.get("published_at")
            return pa.timestamp() if pa else 0
        new_articles.sort(key=_sort_key, reverse=True)
        orig = len(new_articles)
        new_articles = new_articles[:200]
        log.info("Capped new_articles to 200 most recent (was %d)", orig)
        metrics["new_articles"] = len(new_articles)
        metrics["new_articles_capped"] = len(new_articles)

    # Upsert NEW articles into state (preserve first_seen for truly new)
    if new_articles:
        store.upsert_articles(new_articles, now)

    if overnight_only:
        overnight_clusters = cluster_articles(new_articles) if new_articles else []
        store.update_candidate_pool(rank_clusters(overnight_clusters, now), now)
        log.info("Overnight collection: %d new articles stored, no AI/Buffer this run", len(new_articles))
        store.set_last_run({"at": now.isoformat(), "mode": "overnight_collection", "result": f"{len(new_articles)} new"})
        store.set_last_feed_check(now)
        store.prune(now)
        store.save()
        run_summary({**metrics, "result": "overnight_collection"})
        return 0

    if not new_articles and not behind_target:
        log.info("No genuinely new articles, exiting without AI (TEST 1)")
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_new_articles"})
        store.set_last_feed_check(now)
        store.prune(now)
        store.save()
        run_summary({**metrics, "result": "no_new_articles", "ai_calls": 0, "buffer_calls": 0})
        return 0

    # ── Cluster + Rank (+ candidate pool reuse §25) ──
    clusters = cluster_articles(new_articles) if new_articles else []
    metrics["clusters"] = len(clusters)

    # §23 Q5/§25: merge unused candidate pool so behind-quota runs can still act
    pool_entries = []
    if behind_target:
        pool_entries = store.get_candidate_pool(max_age_hours=config.NEWS_MAX_AGE_HOURS)
        metrics["pool_entries"] = len(pool_entries)
        if pool_entries:
            log.info("Catch-up: reusing %d unused candidates from pool", len(pool_entries))

    if not clusters and not pool_entries:
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_clusters"})
        store.set_last_feed_check(now)
        store.save()
        run_summary({**metrics, "result": "no_clusters"})
        return 0

    # Upsert clusters for development tracking
    if clusters:
        store.upsert_clusters(clusters, now)
    ranked = rank_clusters(clusters, now, store.data.get("clusters"))

    # Convert pool entries back into cluster-like dicts (for selection)
    pool_clusters: list[dict] = []
    for e in pool_entries:
        if e["cluster_id"] in {c["cluster_id"] for c in clusters}:
            continue
        pool_clusters.append({
            "cluster_id": e["cluster_id"],
            "representative_title": e.get("representative_title", ""),
            "representative_article": {"summary": e.get("summary", ""), "category": e.get("category", "general"), "published_at": publication_time(e.get("latest_activity"))},
            "sources": e.get("sources", []),
            "source_count": e.get("source_count", 1),
            "member_ids": e.get("article_ids", []),
            "size": len(e.get("article_ids", []) or [1]),
            "latest_activity": publication_time(e.get("latest_activity")),
            "first_detected": publication_time(e.get("latest_activity")),
            "development_level": 1,
            "from_pool": True,
        })
    # Rank pool with the same scorer (pool entries lack articles → text from title only)
    if pool_clusters:
        ranked = rank_clusters(list(ranked) + pool_clusters, now, store.data.get("clusters"))
    ranked = [c for c in ranked if not story_problem(c.get("representative_title", ""), (c.get("representative_article") or {}).get("published_at") or c.get("latest_activity"), now)]

    # Quota-aware candidate count (§12)
    if remaining["target_remaining"] >= 7:
        n_cand = max(top_n, config.TOP_CANDIDATES_CATCHUP)
    elif remaining["target_remaining"] >= 4:
        n_cand = max(5, top_n)
    else:
        n_cand = max(3, min(top_n, 4))
    n_cand = min(n_cand, len(ranked))
    candidates = ranked[:n_cand]
    candidates = [c for c in candidates if c.get("_score", 0) >= score_floor]
    # §images: ensure image-capable candidates exist when image budget remains
    def _cluster_has_image(c: dict) -> bool:
        from app.images import has_known_image
        return has_known_image(c.get("representative_title", ""), store.data)

    image_budget_left = config.ENABLE_IMAGES and store.image_quota_remaining() > 0
    if image_budget_left:
        for c in candidates:
            c["has_image"] = _cluster_has_image(c)
        if not any(c.get("has_image") for c in candidates):
            extras = [c for c in ranked if c not in candidates and _cluster_has_image(c)]
            extras = [c for c in extras if c.get("_score", 0) >= score_floor][:2]
            for c in extras:
                c["has_image"] = True
            if extras:
                log.info("§images: no image-bearing candidates in top-%d — adding %d from deeper ranks", n_cand, len(extras))
                candidates.extend(extras)
        # Prefer image-bearing among near-equal scores (within 15% of top)
        if candidates:
            top_score = candidates[0].get("_score", 0)
            near = [c for c in candidates if c.get("_score", 0) >= top_score * 0.85]
            far = [c for c in candidates if c.get("_score", 0) < top_score * 0.85]
            near.sort(key=lambda c: not c.get("has_image", False))
            candidates = near + far
    metrics["candidates"] = len(candidates)
    if not candidates:
        log.info("No candidates above threshold (%s), exiting without AI", score_floor)
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_candidates"})
        store.set_last_feed_check(now)
        store.save()
        run_summary({**metrics, "result": "no_candidates"})
        return 0
    # Update pool with the current fresh clusters
    if clusters:
        store.update_candidate_pool(candidates, now)

    # ── Capacity gates BEFORE AI (§13) ───────────────
    if ai_call_budget <= 0:
        log.info("AI call budget exhausted (run=%d, day=%d/%d)", ai_call_budget, counts.get("ai_calls", 0), config.MAX_AI_CALLS_PER_DAY)
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "daily_ai_limit"})
        store.save()
        run_summary({**metrics, "result": "daily_ai_limit"})
        return 0
    if remaining["total_remaining"] <= 0:
        log.info("Total daily limit reached (TEST 6)")
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "daily_total_limit"})
        store.save()
        run_summary({**metrics, "result": "daily_total_limit"})
        return 0

    # Buffer queue awareness — only fetch if we have candidates (preserve rate limit)
    # We need queue state to compute available capacity (MAX_BUFFER_AHEAD)
    buffer_queue: list[dict] = []
    # Only query Buffer if we might schedule; otherwise local ledger is enough
    # Heuristic: query if candidates exist and we're within posting window or have a breaking candidate
    has_breaking_candidate = any(c.get("_score", 0) >= 55 and c.get("latest_activity") for c in candidates)
    should_query_buffer = bool(candidates) and (remaining["ai_remaining"] > 0)
    if should_query_buffer and not dry_run:
        buffer_queue = _fetch_buffer_queue(store)
    else:
        # Dry-run: use local ledger only
        buffer_queue = []
        for p in store.data.get("posts", []):
            if p.get("status") == "scheduled":
                try:
                    due = datetime.fromisoformat(p.get("scheduled_at", "").replace("Z", "+00:00"))
                    if due > now:
                        buffer_queue.append({"dueAt": p["scheduled_at"], "id": p.get("buffer_post_id", "")})
                except Exception:
                    continue
    metrics["buffer_queue"] = len(buffer_queue)

    if len(buffer_queue) >= config.MAX_BUFFER_AHEAD_POSTS:
        # Check if any candidate is breaking — breaking may bypass
        if not has_breaking_candidate:
            log.info("Buffer queue at capacity (%d/%d), no breaking candidate — no AI this run (TEST 7)", len(buffer_queue), config.MAX_BUFFER_AHEAD_POSTS)
            store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "queue_full"})
            store.save()
            run_summary({**metrics, "result": "queue_full", "ai_calls": 0})
            return 0
        log.info("Queue at capacity but breaking candidate exists — proceeding")

    # ── AI: editorial select + generation ─────────────
    # Respect per-run AI call budget
    # Build compact candidate payload for editorial prompt
    candidate_payload = []
    for c in candidates:
        rep = c.get("representative_article") or {}
        candidate_payload.append({
            "story_id": c["cluster_id"],
            "title": c.get("representative_title", ""),
            "source": ", ".join(c.get("sources", [])[:3]),
            "published": (c.get("latest_activity") or c.get("first_detected") or now).isoformat() if isinstance(c.get("latest_activity"), datetime) else str(c.get("latest_activity") or ""),
            "summary": (rep.get("summary") or "")[:300],
            "category": rep.get("category", "general"),
            "source_count": c.get("source_count", 1),
            "cluster_sources": c.get("sources", []),
            "score": c.get("_score"),
            "has_image": c.get("has_image", False),
        })

    # Optionally enrich top 2-3 candidates with article text + og:image (capped)
    # Only after passing queue/capacity gates to avoid wasted fetches
    enrich_top_candidates(candidates[:2])

    # Re-inject article_text into payload
    for i, c in enumerate(candidates[:2]):
        if "article_text" in c and i < len(candidate_payload):
            candidate_payload[i]["summary"] = (c["article_text"][:400] or candidate_payload[i]["summary"])

    # Editorial selection
    try:
        from app.ai import editorial_select, generate_post
        _record_ai_call()
        selections = editorial_select(candidate_payload)
        metrics["ai_calls"] = OPENROUTER_CALLS
    except Exception as exc:
        OPENROUTER_FAILURES += 1
        log.error("OpenRouter editorial selection failed: %s", exc)
        # Fallback: use local ranking instead of AI selection (so Buffer can still be tested)
        log.warning("Falling back to local ranking for editorial selection")
        n_fallback = min(remaining["target_remaining"], config.MAX_NEW_POSTS_PER_RUN, len(candidates))
        selections = [
            {"story_id": c["cluster_id"], "decision": "select", "urgency": 60, "format": "NEWS_UPDATE", "reason": "local fallback", "is_new_development": True}
            for c in candidates[: max(1, n_fallback)]
        ]
        metrics["ai_calls"] = OPENROUTER_CALLS
        metrics["ai_fallback"] = True
        # Don't save as error, continue to generation
        if not selections:
            store.set_last_run({"at": now.isoformat(), "mode": "error", "result": f"openrouter_error: {exc}"})
            store.save()
            run_summary({**metrics, "result": "openrouter_error", "ai_calls": OPENROUTER_CALLS, "ai_failures": OPENROUTER_FAILURES})
            return 1

    selected = [s for s in selections if s.get("decision") == "select"]
    # §12: per-run post count scales with remaining quota, hard-capped
    posts_this_run_cap = max(1, min(
        config.MAX_NEW_POSTS_PER_RUN,
        remaining["target_remaining"] if behind_target else 2,
        remaining["hard_max_remaining"],
    ))
    selected = selected[:posts_this_run_cap]
    metrics["ai_selected"] = len(selected)
    if not selected:
        log.info("AI rejected all candidates this run")
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "ai_rejected_all"})
        store.save()
        run_summary({**metrics, "result": "ai_rejected_all"})
        return 0

    # ── Generate posts ───────────────────────────────
    existing_texts = {p.get("text", "") for p in store.data.get("posts", []) if p.get("status") in ("scheduled", "sent", "published")}
    # §9 same-story cross-run dedupe: reject clusters whose title is too similar
    # to any story already scheduled/created in the last 12 hours
    recent_titles: list[str] = []
    cutoff_12h = now - timedelta(hours=12)
    for p in store.data.get("posts", []):
        if p.get("status") in ("scheduled", "sent", "published") and p.get("created_at"):
            try:
                created = datetime.fromisoformat(str(p["created_at"]).replace("Z", "+00:00"))
                if created >= cutoff_12h and p.get("title"):
                    recent_titles.append(p["title"])
            except Exception:
                continue
    def _too_similar_to_recent(cluster_title: str) -> bool:
        return any(duplicate_development(cluster_title, previous) for previous in recent_titles)

    generated: list[dict] = []
    for sel in selected:
        cid = sel.get("story_id")
        fmt = sel.get("format", "NEWS_UPDATE")
        if fmt not in ("BREAKING", "DEVELOPING", "NEWS_UPDATE", "CONTEXT", "KEY_DETAIL"):
            fmt = "NEWS_UPDATE"
        try:
            urgency = max(0, min(100, int(sel.get("urgency", 50))))
        except (TypeError, ValueError):
            urgency = 50
        # Find cluster for this story_id
        cluster = next((c for c in candidates if c["cluster_id"] == cid), None)
        if not cluster:
            log.warning("Selected story %s not in candidates, skipping", cid)
            continue
        published_at = publication_time((cluster.get("representative_article") or {}).get("published_at") or cluster.get("latest_activity"))
        if story_problem(cluster.get("representative_title", ""), published_at):
            continue
        fresh = (datetime.now(timezone.utc) - published_at).total_seconds() <= config.BREAKING_MAX_AGE_MINUTES * 60
        if not fresh and fmt in ("BREAKING", "DEVELOPING"):
            fmt = "NEWS_UPDATE"
        if not fresh:
            urgency = min(urgency, 75)
        # Development check: if cluster was posted recently and no new development, skip
        state_cl = store.data.get("clusters", {}).get(cid, {})
        if state_cl.get("last_posted_at"):
            try:
                last = datetime.fromisoformat(state_cl["last_posted_at"].replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                gap_min = (now - last).total_seconds() / 60
                if gap_min < config.MIN_DEVELOPMENT_GAP_MINUTES and not sel.get("is_new_development"):
                    # Ask AI's is_new_development; if false and gap not met, skip
                    log.info("Skipping %s: posted %.0f min ago, no new development (gap %d required)", cid[:8], gap_min, config.MIN_DEVELOPMENT_GAP_MINUTES)
                    continue
            except Exception:
                pass
        # §9 cross-run same-story dedupe (different sources, same event)
        if cluster.get("representative_title") and _too_similar_to_recent(cluster["representative_title"]):
            log.warning("Skipping %s: title too similar to a story scheduled in the last 12h — %s", cid[:8], cluster["representative_title"][:70])
            store.mark_pool_rejected(cid)
            metrics["skipped_duplicate"] = metrics.get("skipped_duplicate", 0) + 1
            continue

        story_for_gen = {
            "title": clean_headline(cluster.get("representative_title", ""), cluster.get("sources", [])),
            "source": ", ".join(cluster.get("sources", [])[:2]),
            "published": published_at.isoformat(),
            "summary": (cluster.get("representative_article") or {}).get("summary", "") or cluster.get("article_text", "")[:600],
            "cluster_sources": cluster.get("sources", []),
            "category": (cluster.get("representative_article") or {}).get("category", "general"),
        }
        # Build mention context (recent handles for anti-repetition)
        try:
            from app.editorial import build_mention_context
            recent_handles = []
            for p in store.data.get("posts", [])[-8:]:
                if p.get("status") in ("scheduled", "sent"):
                    from app.accounts import extract_mentions
                    recent_handles.extend(extract_mentions(p.get("text", "")))
            mention_ctx = build_mention_context(story_for_gen, recent_handles)
        except Exception:
            mention_ctx = ""
        try:
            if OPENROUTER_CALLS >= ai_call_budget:
                log.warning("AI call budget exhausted (%d/%d), stopping generation", OPENROUTER_CALLS, ai_call_budget)
                break
            _record_ai_call()
            res = generate_post(story_for_gen, fmt, mention_context=mention_ctx)
            # The app owns label and flag placement; the model supplies only copy.
            raw_post = res.get("post", "").strip()
            country_codes = central_country_codes(res.get("country_codes", []), story_for_gen["title"])
            post_text = render_post(raw_post, country_codes, fmt, published_at)
        except Exception as exc:
            OPENROUTER_FAILURES += 1
            log.error("Generation failed for %s: %s", cid[:8], exc)
            # Never publish a raw feed headline/caption when generation fails.
            continue

        # ── Validate ────────────────────────────────
        from app.validate import validate_post as _validate
        from app.normalize import weighted_length as _wl

        # Provide a rewrite closure for the validator
        def _rewrite_fn(story_, fmt_, old_post_, wl_):
            try:
                if OPENROUTER_CALLS >= ai_call_budget:
                    log.info("Skipping AI rewrite: per-run call budget exhausted")
                    return None
                _record_ai_call()
                from app.ai import _chat
                prompt = (
                    f"Rewrite this X post to be ≤{config.MAX_POST_LENGTH} characters (currently {wl_}). "
                    f"Keep the label, flags, named speaker, uncertainty and meaning. No links or publisher credits.\n\nPost:\n{old_post_}"
                )
                raw = _chat([{"role": "user", "content": prompt}], temperature=0.5, max_tokens=400)
                # Extract post — assume raw is the post or JSON with post
                try:
                    from app.ai import _extract_json
                    parsed = _extract_json(raw)
                    if isinstance(parsed, dict) and "post" in parsed:
                        return str(parsed["post"]).strip()
                    if isinstance(parsed, str):
                        return parsed.strip()
                except Exception:
                    pass
                return raw.strip()
            except Exception:
                return None

        ok, final_post, reason = _validate(
            post_text, fmt,
            story={"title": story_for_gen["title"], "published_at": published_at, "sources": cluster.get("sources", []), "country_codes": country_codes, "require_freshness": True},
            existing_post_texts=existing_texts, ai_generate_fn=_rewrite_fn,
        )
        metrics.setdefault("posts_generated", 0)
        if not ok:
            log.warning("Post rejected for %s: %s — %s", cid[:8], reason, post_text[:80])
            metrics["posts_rejected"] = metrics.get("posts_rejected", 0) + 1
            # Record rejected post for history (not counted toward daily limits)
            store.add_post({
                "post_id": f"rejected_{cid[:8]}_{now.strftime('%H%M%S')}",
                "cluster_id": cid,
                "article_ids": cluster.get("member_ids", []),
                "format": fmt,
                "text": final_post[:500],
                "priority": urgency,
                "status": "rejected",
                "failure_reason": reason,
                "created_at": now.isoformat(),
                "day": _today_key(now),
            })
            continue

        if reason in ("rewritten", "accepted-with-warning", "hard-truncated"):
            log.info("Post for %s: %s (%d chars)", cid[:8], reason, _wl(final_post))
        existing_texts.add(final_post)
        recent_titles.append(story_for_gen["title"])

        # ── Image selection (§images) ───────────────
        # Priority: rights-cleared curated media → Commons PD/CC0 → article media.
        image_url = None
        image_metadata = None
        image_urls = []
        if config.ENABLE_IMAGES and store.image_quota_remaining() > 0:
            try:
                from app.images import select_story_images
                assets = select_story_images({"title": final_post, "country_codes": country_codes}, store.data)
                image_urls = [asset["url"] for asset in assets]
                image_url = image_urls[0] if image_urls else None
                image_metadata = [asset.get("metadata") for asset in assets]
            except Exception as img_exc:
                log.warning("Story image selection failed: %s", img_exc)
            if image_url:
                log.info("Image attached to %s (budget left: %d): %s", cid[:8], store.image_quota_remaining(), image_url[:80])
            else:
                log.info("No image found for %s — text-only", cid[:8])

        is_breaking = fmt in ("BREAKING", "DEVELOPING") or urgency >= 90
        generated.append({
            "story_id": cid,
            "cluster": cluster,
            "format": fmt,
            "urgency": urgency,
            "text": final_post,
            "is_breaking": is_breaking,
            "image_url": image_url,
            "image_urls": image_urls,
            "image_metadata": image_metadata,
            "published_at": published_at.isoformat(),
            "country_codes": country_codes,
        })
        metrics["posts_generated"] += 1

    metrics["posts_generated_ok"] = len(generated)
    metrics["ai_calls"] = OPENROUTER_CALLS
    metrics["daily_ai_calls"] = store.daily_counts_for(day_key).get("ai_calls", 0)
    # Deferred/failed drafts remain eligible until successfully accepted by Buffer.
    if not generated:
        # Mark rejections so pool doesn't loop on them
        for sel in selected:
            store.mark_pool_rejected(sel.get("story_id", ""))
        log.info("No valid posts generated this run")
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_valid_posts"})
        store.save()
        run_summary({**metrics, "result": "no_valid_posts", "ai_calls": OPENROUTER_CALLS})
        return 0

    # ── Schedule (§19 dynamic, §18 breaking ASAP) ──
    # Order: breaking first
    generated.sort(key=lambda g: (not g["is_breaking"], -g["urgency"]))
    schedule_items = compute_schedule(
        [{"story_id": g["story_id"], "format": g["format"], "is_breaking": g["is_breaking"], "urgency": g["urgency"]} for g in generated],
        existing_scheduled=buffer_queue,
        now=now,
        remaining_quota=remaining["hard_max_remaining"],
    )
    metrics["scheduled"] = len(schedule_items)
    if not schedule_items:
        log.info("Scheduler produced no slots (horizon/capacity/window), deferring %d posts", len(generated))
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_slots"})
        store.save()
        run_summary({**metrics, "result": "no_slots"})
        return 0

    # Map schedule back to generated items
    scheduled_posts: list[dict] = []
    for item in schedule_items:
        gen = next((g for g in generated if g["story_id"] == item["story"]["story_id"]), None)
        if not gen:
            continue
        due = item["due_at"]
        scheduled_posts.append({
            "post_id": f"post_{gen['story_id'][:8]}_{due.strftime('%Y%m%d%H%M')}",
            "cluster_id": gen["story_id"],
            "title": gen["cluster"].get("representative_title", ""),
            "article_ids": gen["cluster"].get("member_ids", []),
            "sources": gen["cluster"].get("sources", []),
            "format": gen["format"],
            "text": gen["text"],
            "image_url": gen.get("image_url"),
            "image_urls": gen.get("image_urls", []),
            "image_metadata": gen.get("image_metadata"),
            "published_at": gen["published_at"],
            "country_codes": gen["country_codes"],
            "priority": gen["urgency"],
            "scheduled_at": due.isoformat(),
            "due_at_iso": format_due_at(due),
            "is_breaking": gen["is_breaking"],
            "created_at": now.isoformat(),
            "day": _today_key(due),
            "status": "scheduled",
        })

    # ── Daily limit re-check before Buffer sends ───
    # Filter scheduled_posts that would exceed daily caps
    filtered: list[dict] = []
    for p in scheduled_posts:
        day = p["day"]
        counts_day = store.daily_counts_for(day)
        # Check kind
        ok, reason = __import__("app.validate", fromlist=["check_daily_limits"]).check_daily_limits(counts_day, kind="ai")
        if not ok:
            log.warning("Skipping post for %s: %s", p["cluster_id"][:8], reason)
            p["status"] = "rejected"
            p["failure_reason"] = reason
            store.add_post(p)
            metrics["posts_rejected"] = metrics.get("posts_rejected", 0) + 1
            continue
        filtered.append(p)
    scheduled_posts = filtered
    metrics["scheduled_after_limits"] = len(scheduled_posts)

    if not scheduled_posts:
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "daily_limit_after_schedule"})
        store.save()
        run_summary({**metrics, "result": "daily_limit_after_schedule"})
        return 0

    # ── Buffer sends ───────────────────────────────
    if dry_run:
        from copy import deepcopy
        from app.publishing import prepare_post
        preview_store = deepcopy(store)
        occupied = [publication_time(p.get("dueAt") or p.get("scheduled_at")) for p in buffer_queue]
        occupied = [due for due in occupied if due]
        previews = []
        for p in scheduled_posts:
            try:
                urls = prepare_post(p, preview_store, occupied, datetime.now(timezone.utc))
                p["image_urls"] = urls or []
                p["image_url"] = urls[0] if urls else None
                occupied.append(publication_time(p["scheduled_at"]))
                preview_store.increment_daily(p["day"], "ai_scheduled")
                if urls:
                    preview_store.increment_daily(p["day"], "image_posts")
                previews.append(p)
            except ValueError as exc:
                log.info("Dry-run draft deferred: %s", exc)
        scheduled_posts = previews
        log.info("DRY_RUN: would have scheduled %d posts:", len(scheduled_posts))
        for p in scheduled_posts:
            log.info("  [%s] %s due %s — %s\n  Images: %s", p["format"], p["cluster_id"][:8], p["scheduled_at"], p["text"], p.get("image_urls", []))
        # Still record as dry-run scheduled for local ledger? No — don't increment daily counts in dry-run.
        for p in scheduled_posts:
            store.add_post({**p, "status": "dry_run", "buffer_post_id": None})
        store.set_last_run({"at": now.isoformat(), "mode": "dry_run", "result": f"{len(scheduled_posts)} dry-run"})
        store.set_last_feed_check(now)
        store.prune(now)
        store.save()
        run_summary({**metrics, "result": "dry_run", "buffer_calls": 0})
        return 0

    # Real Buffer sends
    ctx = _get_buffer_context(store)
    if not ctx:
        log.error("No Buffer channel available, cannot schedule")
        for p in scheduled_posts:
            p["status"] = "failed"
            p["failure_reason"] = "no_buffer_channel"
            store.add_post(p)
        store.save()
        run_summary({**metrics, "result": "no_buffer_channel"})
        return 1

    successes = 0
    from app.publishing import publish_post
    occupied = []
    for existing in buffer_queue + [p for p in store.data.get("posts", []) if p.get("status") == "scheduled"]:
        due = publication_time(existing.get("dueAt") or existing.get("scheduled_at"))
        if due and due >= datetime.now(timezone.utc) - timedelta(minutes=config.MIN_NORMAL_GAP_MINUTES):
            occupied.append(due)
    for p in scheduled_posts:
        try:
            BUFFER_CALLS += 1
            result = publish_post(p, store, occupied)
            day = p["day"]
            p["buffer_post_id"] = result.get("id")
            p["status"] = "scheduled"
            store.add_post(p)
            store.mark_pool_posted(p["cluster_id"])
            occupied.append(publication_time(p["scheduled_at"]))
            store.increment_daily(day, "ai_scheduled", 1)
            if p.get("image_urls"):
                store.increment_daily(day, "image_posts", 1)
                metrics["image_posts_today"] = store.daily_counts_for(day).get("image_posts", 0)
            store.save()
            successes += 1
        except Exception as exc:
            BUFFER_FAILURES += 1
            log.error("Buffer create failed for %s: %s", p["cluster_id"][:8], exc)
            p["status"] = "failed"
            p["failure_reason"] = str(exc)[:300]
            store.add_post(p)
            # Do NOT increment daily count on failure (TEST 10)
            continue

    metrics["buffer_calls"] = BUFFER_CALLS
    metrics["buffer_failures"] = BUFFER_FAILURES
    metrics["buffer_successes"] = successes

    store.set_last_run({"at": now.isoformat(), "mode": "scheduled" if successes else "buffer_failed", "result": f"{successes}/{len(scheduled_posts)} scheduled"})
    store.set_last_feed_check(now)
    # Persist AI rate-limit memory for chained runs (§13)
    try:
        from app.ai import get_rate_limited_models
        store.data["ai_state"] = {"date": now.strftime("%Y-%m-%d"), "rate_limited": get_rate_limited_models()}
    except Exception:
        pass
    store.prune(now)
    store.save()

    # §28: post-send quota snapshot
    post_counts = _today_counts(store, datetime.now(timezone.utc))
    post_remaining = store.remaining_capacity(_today_key(datetime.now(timezone.utc)))
    run_summary({
        **metrics,
        "result": "scheduled" if successes else "failed",
        "next_due": min((p["scheduled_at"] for p in scheduled_posts if p.get("status") == "scheduled"), default=None),
        "daily_total_after": post_counts["total"],
        "target_remaining_after": post_remaining["target_remaining"],
        "quota_status_after": "TARGET_REACHED" if post_remaining["target_remaining"] <= 0 else "BEHIND_TARGET",
    })
    return 0 if successes or dry_run else 1


def main() -> None:
    args = _parse_args()
    try:
        code = run(dry_run_cli=args.dry_run, force=args.force, chained=args.chained)
    except Exception as exc:
        log.error("Unhandled error: %s", exc)
        traceback.print_exc()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
