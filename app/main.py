"""Orchestrator — collect → decide → schedule → exit. Never sleeps."""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timedelta, timezone

from app import config
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
    return p.parse_args()


def _is_dry_run(cli_dry: bool) -> bool:
    if cli_dry:
        return True
    return config.EFFECTIVE_DRY_RUN


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


def run(dry_run_cli: bool = False, force: bool = False) -> int:
    global OPENROUTER_CALLS, OPENROUTER_FAILURES, BUFFER_CALLS, BUFFER_FAILURES
    OPENROUTER_CALLS = OPENROUTER_FAILURES = BUFFER_CALLS = BUFFER_FAILURES = 0
    now = datetime.now(timezone.utc)
    dry_run = _is_dry_run(dry_run_cli)
    store = StateStore()
    store.load()

    metrics: dict = {
        "run_at": now.isoformat(),
        "dry_run": dry_run,
        "forced": force,
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

    # ── Daily capacity pre-check ─────────────────────
    day_key = _today_key(now)
    counts = _today_counts(store, now)
    metrics["daily_ai"] = counts["ai_scheduled"]
    metrics["daily_total"] = counts["total"]
    remaining = store.remaining_capacity(day_key)
    metrics["ai_remaining"] = remaining["ai_remaining"]
    metrics["total_remaining"] = remaining["total_remaining"]

    # ── Collect ──────────────────────────────────────
    # Snapshot history sets BEFORE collect for dedupe_against_history
    seen_canonical = store.seen_canonical_set()
    seen_hashes = store.seen_title_hashes()
    articles, feed_stats = collect_articles()
    metrics["feeds_attempted"] = feed_stats["attempted"]
    metrics["feeds_succeeded"] = feed_stats["succeeded"]
    metrics["feeds_failed"] = feed_stats["failed"]
    metrics["articles_collected"] = len(articles)

    if feed_stats["failed"] > 0:
        # Still continue — one broken feed doesn't kill run
        log.warning("Feeds failed this run: %s", feed_stats["failed_details"])

    if not articles:
        log.info("No articles collected, exiting")
        store.set_last_run({"at": now.isoformat(), "mode": "collect_only", "result": "no_articles"})
        store.set_last_feed_check(now)
        store.save()
        run_summary({**metrics, "result": "no_articles"})
        return 0

    # ── Dedupe ───────────────────────────────────────
    articles, url_dups = dedupe_by_url(articles)
    metrics["url_dups"] = url_dups
    articles, title_dups = dedupe_by_title(articles)
    metrics["title_dups"] = title_dups
    # History-aware dedupe — only keep genuinely new articles for clustering
    new_articles, history_dups = dedupe_against_history(articles, seen_canonical, seen_hashes)
    metrics["history_dups"] = history_dups
    metrics["new_articles"] = len(new_articles)

    # Upsert NEW articles into state (preserve first_seen for truly new)
    if new_articles:
        store.upsert_articles(new_articles, now)

    if overnight_only:
        log.info("Overnight collection: %d new articles stored, no AI/Buffer this run", len(new_articles))
        store.set_last_run({"at": now.isoformat(), "mode": "overnight_collection", "result": f"{len(new_articles)} new"})
        store.set_last_feed_check(now)
        store.prune(now)
        store.save()
        run_summary({**metrics, "result": "overnight_collection"})
        return 0

    if not new_articles:
        log.info("No genuinely new articles, exiting without AI (TEST 1)")
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_new_articles"})
        store.set_last_feed_check(now)
        store.prune(now)
        store.save()
        run_summary({**metrics, "result": "no_new_articles", "ai_calls": 0, "buffer_calls": 0})
        return 0

    # ── Cluster + Rank ───────────────────────────────
    clusters = cluster_articles(new_articles)
    metrics["clusters"] = len(clusters)
    if not clusters:
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_clusters"})
        store.set_last_feed_check(now)
        store.save()
        run_summary({**metrics, "result": "no_clusters"})
        return 0

    # Upsert clusters for development tracking
    store.upsert_clusters(clusters, now)
    ranked = rank_clusters(clusters, now, store.data.get("clusters"))
    # Take top candidates
    top_n = min(config.TOP_CANDIDATES_PER_RUN, len(ranked))
    candidates = ranked[:top_n]
    # Filter: skip very low scores (noise)
    candidates = [c for c in candidates if c.get("_score", 0) >= 10]
    metrics["candidates"] = len(candidates)
    if not candidates:
        log.info("No candidates above threshold, exiting without AI")
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_candidates"})
        store.set_last_feed_check(now)
        store.save()
        run_summary({**metrics, "result": "no_candidates"})
        return 0

    # ── Capacity gates BEFORE AI (spec §18, §27) ────
    if remaining["ai_remaining"] <= 0:
        log.info("AI daily limit reached, no AI calls this run (TEST 6)")
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
        })

    # Optionally enrich top 2-3 candidates with article text (capped)
    # Only after passing queue/capacity gates to avoid wasted fetches
    enrich_top_candidates(candidates[:2])

    # Re-inject article_text into payload
    for i, c in enumerate(candidates[:2]):
        if "article_text" in c and i < len(candidate_payload):
            candidate_payload[i]["summary"] = (c["article_text"][:400] or candidate_payload[i]["summary"])

    # Editorial selection
    try:
        from app.ai import editorial_select, generate_post
        OPENROUTER_CALLS += 1
        selections = editorial_select(candidate_payload)
        metrics["ai_calls"] = OPENROUTER_CALLS
    except Exception as exc:
        OPENROUTER_FAILURES += 1
        log.error("OpenRouter editorial selection failed: %s", exc)
        store.set_last_run({"at": now.isoformat(), "mode": "error", "result": f"openrouter_error: {exc}"})
        store.save()
        run_summary({**metrics, "result": "openrouter_error", "ai_calls": OPENROUTER_CALLS, "ai_failures": OPENROUTER_FAILURES})
        return 1  # non-zero so Actions reports failure but state is saved

    selected = [s for s in selections if s.get("decision") == "select"]
    # Cap to per-run limit
    selected = selected[: config.MAX_NEW_POSTS_PER_RUN]
    metrics["ai_selected"] = len(selected)
    if not selected:
        log.info("AI rejected all candidates this run")
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "ai_rejected_all"})
        store.save()
        run_summary({**metrics, "result": "ai_rejected_all"})
        return 0

    # ── Generate posts ───────────────────────────────
    existing_texts = {p.get("text", "") for p in store.data.get("posts", [])}
    generated: list[dict] = []
    for sel in selected:
        cid = sel.get("story_id")
        fmt = sel.get("format", "NEWS_UPDATE")
        urgency = sel.get("urgency", 50)
        # Find cluster for this story_id
        cluster = next((c for c in candidates if c["cluster_id"] == cid), None)
        if not cluster:
            log.warning("Selected story %s not in candidates, skipping", cid)
            continue
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

        story_for_gen = {
            "title": cluster.get("representative_title", ""),
            "source": ", ".join(cluster.get("sources", [])[:2]),
            "published": str(cluster.get("latest_activity") or cluster.get("first_detected") or ""),
            "summary": (cluster.get("representative_article") or {}).get("summary", "") or cluster.get("article_text", "")[:600],
            "cluster_sources": cluster.get("sources", []),
            "category": (cluster.get("representative_article") or {}).get("category", "general"),
        }
        try:
            OPENROUTER_CALLS += 1
            if OPENROUTER_CALLS > config.MAX_AI_CALLS_PER_RUN:
                log.warning("AI call budget exceeded (%d/%d), stopping generation", OPENROUTER_CALLS, config.MAX_AI_CALLS_PER_RUN)
                break
            res = generate_post(story_for_gen, fmt)
            post_text = res["post"]
        except Exception as exc:
            OPENROUTER_FAILURES += 1
            log.error("Generation failed for %s: %s", cid[:8], exc)
            # Fallback: generate a simple post from title without AI, so Buffer can still be tested
            fallback_label = __import__("app.editorial", fromlist=["FORMAT_LABELS"]).FORMAT_LABELS.get(fmt, fmt)
            fallback_text = f"{fallback_label} {story_for_gen.get('title','')[:180]}"
            # Ensure it validates (truncate if needed)
            from app.normalize import weighted_length as _wl2
            if _wl2(fallback_text) > config.HARD_MAX_POST_LENGTH:
                fallback_text = fallback_text[: config.HARD_MAX_POST_LENGTH - 1] + "…"
            log.warning("Using fallback post for %s: %s", cid[:8], fallback_text[:80])
            post_text = fallback_text
            # Continue to validation with fallback

        # ── Validate ────────────────────────────────
        from app.validate import validate_post as _validate
        from app.normalize import weighted_length as _wl

        # Provide a rewrite closure for the validator
        def _rewrite_fn(story_, fmt_, old_post_, wl_):
            try:
                from app.ai import _chat
                prompt = (
                    f"Rewrite this X post to be ≤{config.MAX_POST_LENGTH} characters (currently {wl_}). "
                    f"Keep the format prefix and meaning, make it shorter.\n\nPost:\n{old_post_}"
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

        ok, final_post, reason = _validate(post_text, fmt, story={"published_at": cluster.get("latest_activity")}, existing_post_texts=existing_texts, ai_generate_fn=_rewrite_fn)
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
        is_breaking = fmt in ("BREAKING", "DEVELOPING") or urgency >= 90
        generated.append({
            "story_id": cid,
            "cluster": cluster,
            "format": fmt,
            "urgency": urgency,
            "text": final_post,
            "is_breaking": is_breaking,
        })
        metrics["posts_generated"] += 1

    metrics["posts_generated_ok"] = len(generated)
    if not generated:
        log.info("No valid posts generated this run")
        store.set_last_run({"at": now.isoformat(), "mode": "idle", "result": "no_valid_posts"})
        store.save()
        run_summary({**metrics, "result": "no_valid_posts", "ai_calls": OPENROUTER_CALLS})
        return 0

    # ── Schedule ───────────────────────────────────
    # Order: breaking first
    generated.sort(key=lambda g: (not g["is_breaking"], -g["urgency"]))
    schedule_items = compute_schedule(
        [{"story_id": g["story_id"], "format": g["format"], "is_breaking": g["is_breaking"], "urgency": g["urgency"]} for g in generated],
        existing_scheduled=buffer_queue,
        now=now,
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
            "article_ids": gen["cluster"].get("member_ids", []),
            "format": gen["format"],
            "text": gen["text"],
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
        log.info("DRY_RUN: would have scheduled %d posts:", len(scheduled_posts))
        for p in scheduled_posts:
            log.info("  [%s] %s due %s — %s", p["format"], p["cluster_id"][:8], p["scheduled_at"], p["text"][:80])
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
    for p in scheduled_posts:
        # Re-validate dueAt is still safely in the future (Buffer requires >~60s, clock skew + AI latency can make original slot stale)
        # Ensure at least 6 minutes future so Buffer never rejects "must be in the future" (add margin for server clock)
        try:
            due_dt = datetime.fromisoformat(p["due_at_iso"].replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            min_future = now_utc + timedelta(minutes=6)
            log.info("Buffer send check %s: due %s, now %s, min_future %s", p["cluster_id"][:8], p["due_at_iso"], format_due_at(now_utc), format_due_at(min_future))
            if due_dt <= min_future:
                bumped = min_future + timedelta(seconds=30)
                log.warning("Bumping %s dueAt %s → %s (was too close to now, Buffer requires future)", p["cluster_id"][:8], p["due_at_iso"], format_due_at(bumped))
                p["due_at_iso"] = format_due_at(bumped)
                p["scheduled_at"] = bumped.isoformat()
                p["day"] = _today_key(bumped)
        except Exception as exc:
            log.warning("DueAt bump check failed for %s: %s", p["cluster_id"][:8], exc)
        # Re-check total limit right before each send (race with queue)
        day = p["day"]
        counts_day = store.daily_counts_for(day)
        from app.validate import check_daily_limits as _check
        ok, reason = _check(counts_day, kind="ai")
        if not ok:
            log.warning("Skipping %s before send: %s", p["cluster_id"][:8], reason)
            p["status"] = "rejected"
            p["failure_reason"] = reason
            store.add_post(p)
            continue
        try:
            BUFFER_CALLS += 1
            from app.buffer import create_scheduled_post
            try:
                result = create_scheduled_post(ctx["channel_id"], p["text"], p["due_at_iso"])
            except Exception as first_exc:
                # Retry once with a later due if Buffer says "must be in the future" (clock skew)
                if "future" in str(first_exc).lower():
                    bumped2 = datetime.now(timezone.utc) + timedelta(minutes=7)
                    new_due = format_due_at(bumped2)
                    log.warning("Buffer rejected dueAt in past, retrying %s with %s", p["cluster_id"][:8], new_due)
                    p["due_at_iso"] = new_due
                    p["scheduled_at"] = bumped2.isoformat()
                    p["day"] = _today_key(bumped2)
                    result = create_scheduled_post(ctx["channel_id"], p["text"], new_due)
                else:
                    raise
            p["buffer_post_id"] = result.get("id")
            p["status"] = "scheduled"
            store.add_post(p)
            store.increment_daily(day, "ai_scheduled", 1)
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
    store.prune(now)
    store.save()

    next_due = min((p["scheduled_at"] for p in scheduled_posts if p.get("status") == "scheduled"), default=None)
    run_summary({**metrics, "result": "scheduled" if successes else "failed", "next_due": next_due})
    return 0 if successes or dry_run else 1


def main() -> None:
    args = _parse_args()
    try:
        code = run(dry_run_cli=args.dry_run, force=args.force)
    except Exception as exc:
        log.error("Unhandled error: %s", exc)
        traceback.print_exc()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
