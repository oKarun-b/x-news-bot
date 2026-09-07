"""State persistence — JSON-backed repository with SQLite-ready interface."""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any

from app import config
from app.logging_setup import get_logger

log = get_logger("x-news-bot.db")

# ── Helpers ──────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_key(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(config.BOT_TIMEZONE)
    else:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(config.BOT_TIMEZONE)
    return now.date().isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ── State shape ──────────────────────────────────────

DEFAULT_STATE: dict[str, Any] = {
    "version": 2,
    "updated_at": None,
    "last_run": None,
    "last_successful_feed_check": None,
    "buffer_cache": {},
    "daily_counts": {},
    "candidate_pool": [],
    "articles": {},   # id -> article record
    "clusters": {},   # cluster_id -> cluster record
    "posts": [],      # list of post records
}


class StateStore:
    """
    JSON-backed state store. Interface is intentionally small so a SQLite
    implementation can replace it without touching callers.
    """

    def __init__(self, path: pathlib.Path | None = None):
        self.path = pathlib.Path(path) if path else config.STATE_PATH
        self.data: dict[str, Any] = {}
        self._dirty = False

    # -- load / save -------------------------------------------------------

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.data = json.loads(json.dumps(DEFAULT_STATE))  # deep copy
            return self.data
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            # Ensure keys exist (migration-safe)
            for k, v in DEFAULT_STATE.items():
                if k not in self.data:
                    self.data[k] = json.loads(json.dumps(v))
            # Migrate legacy article format to compact (one-time)
            migrated = 0
            for aid, rec in list(self.data.get("articles", {}).items()):
                if "u" not in rec and "canonical_url" in rec:
                    from app.normalize import normalize_title, title_hash
                    rec["u"] = rec.pop("canonical_url", "")
                    rec["h"] = title_hash(normalize_title(rec.pop("title", "")))
                    rec["p"] = rec.pop("published_at", rec.pop("published", "") if "published" in rec else "")
                    rec["f"] = rec.pop("first_seen_at", rec.get("f", ""))
                    rec["l"] = rec.pop("last_seen_at", rec.get("l", ""))
                    # Drop verbose fields if present
                    rec.pop("source", None)
                    rec.pop("tier", None)
                    rec.pop("category", None)
                    rec.pop("feed_name", None)
                    # Keep truncated title for debug only
                    if "title" not in rec and rec.get("h"):
                        rec["id"] = aid
                    migrated += 1
            if migrated:
                log.info("Migrated %d legacy articles to compact format", migrated)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("State load failed (%s), starting fresh", exc)
            self.data = json.loads(json.dumps(DEFAULT_STATE))
        return self.data

    def save(self) -> bool:
        """Write if dirty or if content differs from file. Returns True if written."""
        self.data["updated_at"] = _now_iso()
        # Ensure dir exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Check if changed (avoid empty commits)
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                # compare without updated_at (always changes)
                a = {k: v for k, v in self.data.items() if k != "updated_at"}
                b = {k: v for k, v in existing.items() if k != "updated_at"}
                if a == b:
                    return False
            except Exception:
                pass
        with open(self.path, "w", encoding="utf-8") as f:
            # Compact JSON to keep repo size small (2.3 MB -> ~0.8 MB)
            json.dump(self.data, f, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
            f.write("\n")
        self._dirty = False
        log.info("State saved to %s (%d bytes)", self.path, self.path.stat().st_size)
        return True

    # -- articles ----------------------------------------------------------

    def upsert_articles(self, articles: list[dict], now: datetime | None = None) -> dict[str, int]:
        """Insert/update articles, tracking first_seen/last_seen. Returns counts. Compact storage."""
        if now is None:
            now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        arts: dict[str, Any] = self.data.setdefault("articles", {})
        from app.normalize import normalize_title, title_hash
        new = 0
        updated = 0
        for a in articles:
            aid = a["id"]
            existing = arts.get(aid)
            if existing is None:
                # Compact: store only essentials for dedupe (u=canonical, h=title hash, p=published)
                arts[aid] = {
                    "u": a.get("canonical_url", ""),
                    "h": title_hash(normalize_title(a.get("title", ""))),
                    "p": a.get("published", ""),
                    "f": now_iso,
                    "l": now_iso,
                }
                new += 1
            else:
                # Only bump last_seen if older than 1 hour (reduces state churn and git bloat)
                last_str = existing.get("l", existing.get("last_seen_at", ""))
                last = _parse_iso(last_str)
                if last is None or (now - last).total_seconds() > 3600:
                    if "l" in existing:
                        existing["l"] = now_iso
                    else:
                        existing["last_seen_at"] = now_iso
                    updated += 1
                # else: skip update, keep existing last_seen to avoid churn
        return {"new": new, "updated": updated, "total": len(arts)}

    def seen_canonical_set(self) -> set[str]:
        vals = self.data.get("articles", {}).values()
        return {v.get("u", v.get("canonical_url", "")) for v in vals if v.get("u") or v.get("canonical_url")}

    def seen_title_hashes(self) -> set[str]:
        vals = self.data.get("articles", {}).values()
        # Prefer stored hash (h), fallback to computing from title for legacy
        hashes = set()
        for v in vals:
            if v.get("h"):
                hashes.add(v["h"])
            elif v.get("title"):
                from app.normalize import normalize_title, title_hash
                hashes.add(title_hash(normalize_title(v.get("title", ""))))
        return hashes

    # -- clusters ----------------------------------------------------------

    def upsert_clusters(self, clusters: list[dict], now: datetime | None = None) -> None:
        if now is None:
            now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        store: dict[str, Any] = self.data.setdefault("clusters", {})
        for c in clusters:
            cid = c["cluster_id"]
            prev = store.get(cid)
            if prev is None:
                store[cid] = {
                    "cluster_id": cid,
                    "member_ids": c.get("member_ids", []),
                    "sources": c.get("sources", []),
                    "source_count": c.get("source_count", 1),
                    "representative_title": c.get("representative_title", ""),
                    "size": c.get("size", 1),
                    "first_detected": now_iso,
                    "latest_activity": now_iso,
                    "development_level": 1,
                    "posts_generated": 0,
                    "last_posted_at": None,
                    "score": c.get("_score"),
                }
            else:
                # Update activity & source count if expanded
                prev["latest_activity"] = now_iso
                # Merge sources
                merged_sources = sorted(set(prev.get("sources", [])) | set(c.get("sources", [])))
                prev["sources"] = merged_sources
                prev["source_count"] = len(merged_sources)
                prev["size"] = max(prev.get("size", 1), c.get("size", 1))
                # Development level bump if new members/sources appeared
                if len(c.get("member_ids", [])) > len(prev.get("member_ids", [])):
                    prev["development_level"] = prev.get("development_level", 1) + 1
                prev["score"] = c.get("_score", prev.get("score"))

    # -- posts -------------------------------------------------------------

    def add_post(self, post: dict) -> None:
        posts: list[dict] = self.data.setdefault("posts", [])
        posts.append(post)
        # Update cluster bookkeeping
        cid = post.get("cluster_id")
        if cid:
            clusters = self.data.get("clusters", {})
            cl = clusters.get(cid)
            if cl is not None:
                cl["posts_generated"] = cl.get("posts_generated", 0) + 1
                cl["last_posted_at"] = post.get("created_at") or _now_iso()
                if post.get("status") == "scheduled":
                    # bump development level after successful schedule
                    pass

    def daily_counts_for(self, day: str | None = None) -> dict[str, int]:
        if day is None:
            day = _today_key()
        dc = self.data.get("daily_counts", {}).get(day, {})
        return {
            "ai_scheduled": dc.get("ai_scheduled", 0),
            "fixed_scheduled": dc.get("fixed_scheduled", 0),
            "total": dc.get("total", 0),
            "published": dc.get("published", 0),
            "ai_calls": dc.get("ai_calls", 0),
            "rejected": dc.get("rejected", 0),
            "failed": dc.get("failed", 0),
        }

    def increment_daily(self, day: str, kind: str = "ai_scheduled", amount: int = 1) -> None:
        dc = self.data.setdefault("daily_counts", {})
        rec = dc.setdefault(day, {"ai_scheduled": 0, "fixed_scheduled": 0, "total": 0, "published": 0, "ai_calls": 0, "rejected": 0, "failed": 0})
        rec[kind] = rec.get(kind, 0) + amount
        if kind in ("ai_scheduled", "fixed_scheduled"):
            rec["total"] = rec.get("ai_scheduled", 0) + rec.get("fixed_scheduled", 0)

    def remaining_capacity(self, day: str | None = None) -> dict[str, int]:
        if day is None:
            day = _today_key()
        counts = self.daily_counts_for(day)
        return {
            "ai_remaining": max(0, config.MAX_AI_POSTS_PER_DAY - counts["ai_scheduled"]),
            "total_remaining": max(0, config.MAX_TOTAL_POSTS_PER_DAY - counts["total"]),
            # §3: remaining to reach DAILY_POST_TARGET
            "target_remaining": max(0, config.DAILY_POST_TARGET - counts["total"]),
            "hard_max_remaining": max(0, config.DAILY_POST_HARD_MAX - counts["total"]),
        }

    # -- candidate pool (§23 Q5: stories still unused) -----------------

    def update_candidate_pool(self, clusters: list[dict], now: datetime | None = None) -> None:
        """Merge fresh clusters into the rolling candidate pool (cap 20)."""
        if now is None:
            now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        pool: list[dict] = self.data.setdefault("candidate_pool", [])
        by_id = {c.get("cluster_id"): c for c in pool}
        for c in clusters:
            cid = c["cluster_id"]
            entry = {
                "cluster_id": cid,
                "representative_title": c.get("representative_title", ""),
                "sources": c.get("sources", []),
                "source_count": c.get("source_count", 1),
                "score": c.get("_score"),
                "added_at": now_iso,
                "reject_count": by_id.get(cid, {}).get("reject_count", 0),
                "article_ids": c.get("member_ids", []),
                "category": (c.get("representative_article") or {}).get("category", "general"),
                "summary": (c.get("representative_article") or {}).get("summary", "")[:400],
                "latest_activity": (c.get("latest_activity").isoformat() if isinstance(c.get("latest_activity"), datetime) else str(c.get("latest_activity") or "")),
            }
            # Replace or add
            by_id[cid] = entry
        merged = sorted(by_id.values(), key=lambda x: x.get("score") or 0, reverse=True)[:20]
        self.data["candidate_pool"] = merged

    def get_candidate_pool(self, max_age_hours: float | None = None) -> list[dict]:
        """Return pool entries not too old (and not posted)."""
        pool = self.data.get("candidate_pool", [])
        now = datetime.now(timezone.utc)
        out = []
        for e in pool:
            if e.get("posted"):
                continue
            if max_age_hours is not None and e.get("added_at"):
                added = _parse_iso(e["added_at"])
                if added and (now - added).total_seconds() / 3600 > max_age_hours:
                    continue
            out.append(e)
        return out

    def mark_pool_posted(self, cluster_id: str) -> None:
        for e in self.data.get("candidate_pool", []):
            if e.get("cluster_id") == cluster_id:
                e["posted"] = True

    def mark_pool_rejected(self, cluster_id: str) -> None:
        for e in self.data.get("candidate_pool", []):
            if e.get("cluster_id") == cluster_id:
                e["reject_count"] = e.get("reject_count", 0) + 1

    # -- run bookkeeping ---------------------------------------------------

    def set_last_run(self, info: dict) -> None:
        self.data["last_run"] = info

    def set_last_feed_check(self, when: datetime | None = None) -> None:
        self.data["last_successful_feed_check"] = (when or datetime.now(timezone.utc)).isoformat()

    def set_last_discovery(self, when: datetime | None = None) -> None:
        self.data["last_discovery_time"] = (when or datetime.now(timezone.utc)).isoformat()

    # -- pruning -----------------------------------------------------------

    def prune(self, now: datetime | None = None) -> dict[str, int]:
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=config.STATE_RETENTION_DAYS)
        article_cutoff = now - timedelta(days=config.ARTICLE_RETENTION_DAYS)
        arts: dict[str, Any] = self.data.get("articles", {})
        removed_articles = 0
        for aid in list(arts.keys()):
            last = _parse_iso(arts[aid].get("l", arts[aid].get("last_seen_at")))
            if last and last < article_cutoff:
                del arts[aid]
                removed_articles += 1

        clusters: dict[str, Any] = self.data.get("clusters", {})
        removed_clusters = 0
        for cid in list(clusters.keys()):
            last = _parse_iso(clusters[cid].get("latest_activity"))
            if last and last < cutoff:
                del clusters[cid]
                removed_clusters += 1

        # Keep posts for a bit longer for published history (7d + extra)
        posts: list[dict] = self.data.get("posts", [])
        keep_posts: list[dict] = []
        removed_posts = 0
        for p in posts:
            created = _parse_iso(p.get("created_at"))
            if created and created < cutoff:
                removed_posts += 1
            else:
                keep_posts.append(p)
        self.data["posts"] = keep_posts

        # Prune old daily_counts beyond retention
        dc: dict[str, Any] = self.data.get("daily_counts", {})
        for day in list(dc.keys()):
            try:
                d = datetime.fromisoformat(day).replace(tzinfo=config.BOT_TIMEZONE)
                if d < cutoff:
                    del dc[day]
            except Exception:
                pass

        if removed_articles or removed_clusters or removed_posts:
            log.info("Pruned: %d articles, %d clusters, %d posts", removed_articles, removed_clusters, removed_posts)
        return {"articles": removed_articles, "clusters": removed_clusters, "posts": removed_posts}

    # -- fixed posts -------------------------------------------------------

    def load_fixed_posts(self) -> list[dict]:
        p = config.FIXED_POSTS_PATH
        if not p.exists():
            return []
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("posts", []) if isinstance(data, dict) else []
        except Exception as exc:
            log.warning("Failed to load fixed posts: %s", exc)
            return []
