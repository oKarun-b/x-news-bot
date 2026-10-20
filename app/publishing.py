"""Final freshness, spacing, quota and media checks at the publishing boundary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import config
from app.content import publication_time, render_post, story_problem
from app.database import _today_key
from app.scheduler import final_due_at, format_due_at
from app.validate import check_daily_limits, validate_post


def prepare_post(post: dict, store, occupied: list[datetime], now: datetime) -> list[str] | None:
    due = final_due_at(publication_time(post["scheduled_at"]), occupied, now, post["is_breaking"])
    problem = story_problem(post["title"], post.get("published_at"), due)
    if problem:
        raise ValueError(problem)
    published = publication_time(post["published_at"])
    if post["is_breaking"] and (due - published).total_seconds() > config.BREAKING_MAX_AGE_MINUTES * 60:
        raise ValueError("breaking story will be too old at publication")
    day = _today_key(due)
    allowed, reason = check_daily_limits(store.daily_counts_for(day))
    if not allowed or store.remaining_capacity(day)["hard_max_remaining"] <= 0:
        raise ValueError(reason if not allowed else "daily hard maximum reached")
    codes = post.get("country_codes", [])
    # Refresh the label after AI latency; also use publication-time freshness.
    from app.content import visible_label
    text = render_post(post["text"], codes, post["format"], published)
    label = visible_label(post["format"], published, due)
    text = label + text[text.index(":") + 1:]
    ok, text, reason = validate_post(text, post["format"], {
        "title": post["title"], "published_at": published, "sources": post.get("sources", []),
        "country_codes": codes, "require_freshness": True, "validation_time": due,
    })
    if not ok:
        raise ValueError(reason)
    post.update(text=text, scheduled_at=due.isoformat(), due_at_iso=format_due_at(due), day=day)
    urls = post.get("image_urls") or ([post["image_url"]] if post.get("image_url") else [])
    return urls[:min(2, config.MAX_IMAGES_PER_POST)] if config.ENABLE_IMAGES and store.image_quota_remaining(day) > 0 and urls else None


def publish_post(post: dict, store, occupied: list[datetime]) -> dict:
    from app.buffer import create_scheduled_post
    images = prepare_post(post, store, occupied, datetime.now(timezone.utc))
    # Only explicit API rejections may be retried here. Unknown outcomes propagate.
    retried_time = retried_image = False
    for _ in range(3):
        try:
            result = create_scheduled_post(
                store.data["buffer_cache"]["channel_id"], post["text"], post["due_at_iso"],
                image_urls=images, source_names=post.get("sources", []),
            )
            post["image_urls"] = images or []
            post["image_url"] = images[0] if images else None
            if not images:
                post["image_metadata"] = []
            return result
        except RuntimeError as exc:
            message = str(exc).lower()
            if "createpost failed" not in message:
                raise
            if "future" in message and not retried_time:
                retried_time = True
                post["scheduled_at"] = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
                images = prepare_post(post, store, occupied, datetime.now(timezone.utc))
                if retried_image:
                    images = None
            elif images and any(word in message for word in ("image", "asset", "fetch")) and not retried_image:
                retried_image = True
                images = None
            else:
                raise
    raise RuntimeError("Buffer retry limit reached")
