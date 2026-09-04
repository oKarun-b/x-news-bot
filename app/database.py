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
    "version": 1,
    "updated_at": None,
    "last_run": None,
    "last_successful_feed_check": None,
    "buffer_cache": {},
    "daily_counts": {},
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
            json.dump(self.data, f, indent=2, ensure_ascii=False, sort_keys=False)
            f.write("\n")
        self._dirty = False
        log.info("State saved to %s", self.path)
        return True

    # -- articles ----------------------------------------------------------

    def upsert_articles(self, articles: list[dict], now: datetime | None = None) -> dict[str, int]:
        """Insert/update articles, tracking first_seen/last_seen. Returns counts."""
        if now is None:
            now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        arts: dict[str, Any] = self.data.setdefault("articles", {})
        new = 0
        updated = 0
        for a in articles:
            aid = a["id"]
            existing = arts.get(aid)
            if existing is None:
                arts[aid] = {
                    "id": aid,
                    "canonical_url": a.get("canonical_url", ""),
                    "title": a.get("title", ""),
                    "source": a.get("source", ""),
                    "tier": a.get("tier", 3),
                    "category": a.get("category", "general"),
                    "published_at": a.get("published", ""),
                    "first_seen_at": now_iso,
                    "last_seen_at": now_iso,
                    "feed_name": a.get("feed_name", ""),
                }
                new += 1
            else:
                existing["last_seen_at"] = now_iso
                updated += 1
        return {"new": new, "updated": updated, "total": len(arts)}

    def seen_canonical_set(self) -> set[str]:
        return {v.get("canonical_url", "") for v in self.data.get("articles", {}).values() if v.get("canonical_url")}

    def seen_title_hashes(self) -> set[str]:
        from app.normalize import normalize_title, title_hash
        return {title_hash(normalize_title(v.get("title", ""))) for v in self.data.get("articles", {}).values()}

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
        }

    def increment_daily(self, day: str, kind: str = "ai_scheduled", amount: int = 1) -> None:
        dc = self.data.setdefault("daily_counts", {})
        rec = dc.setdefault(day, {"ai_scheduled": 0, "fixed_scheduled": 0, "total": 0, "published": 0})
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
        }

    # -- run bookkeeping ---------------------------------------------------

    def set_last_run(self, info: dict) -> None:
        self.data["last_run"] = info

    def set_last_feed_check(self, when: datetime | None = None) -> None:
        self.data["last_successful_feed_check"] = (when or datetime.now(timezone.utc)).isoformat()

    # -- pruning -----------------------------------------------------------

    def prune(self, now: datetime | None = None) -> dict[str, int]:
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=config.STATE_RETENTION_DAYS)
        arts: dict[str, Any] = self.data.get("articles", {})
        removed_articles = 0
        for aid in list(arts.keys()):
            last = _parse_iso(arts[aid].get("last_seen_at"))
            if last and last < cutoff:
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
