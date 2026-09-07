"""§32 tests — quota engine, freshness bands, geo priority, catch-up, scheduling."""
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import config
from app.rank import score_cluster, rank_clusters, freshness_score
from app.scheduler import compute_schedule, _dynamic_gap
from app.validate import validate_post


def _cl(title, cid="c1", age_min=20, tier=1, source_count=2, dev=1, cat="us"):
    now = datetime.now(timezone.utc)
    pub = now - timedelta(minutes=age_min)
    return {
        "cluster_id": cid,
        "representative_title": title,
        "representative_article": {"summary": title, "category": cat},
        "articles": [{"tier": tier, "category": cat, "title": title, "summary": ""} for _ in range(source_count)],
        "member_ids": [cid],
        "sources": [f"Src{i}" for i in range(source_count)],
        "source_count": source_count,
        "size": source_count,
        "latest_activity": pub,
        "first_detected": pub,
        "development_level": dev,
    }


# ── §3/§5/§25 quota ──────────────────────────────────

def test_quota_target_defaults():
    assert config.DAILY_POST_TARGET == 10
    assert config.DAILY_POST_MINIMUM == 10
    assert config.DAILY_POST_HARD_MAX == 14


def test_remaining_quota_math():
    import pathlib, tempfile
    from app.database import StateStore
    s = StateStore(pathlib.Path(tempfile.mkdtemp()) / "s.json")
    s.load()
    day = "2024-06-01"
    # 3 scheduled → remaining 7
    s.increment_daily(day, "ai_scheduled", 3)
    rem = s.remaining_capacity(day)
    assert rem["target_remaining"] == 7
    # 10 → target reached, hard max not
    s.increment_daily(day, "ai_scheduled", 7)
    rem = s.remaining_capacity(day)
    assert rem["target_remaining"] == 0
    assert rem["hard_max_remaining"] == 4
    # 14 → hard max reached
    s.increment_daily(day, "ai_scheduled", 4)
    rem = s.remaining_capacity(day)
    assert rem["hard_max_remaining"] == 0


def test_catch_up_mode():
    import pathlib, tempfile
    from app.database import StateStore
    s = StateStore(pathlib.Path(tempfile.mkdtemp()) / "s.json")
    s.load()
    day = "2024-06-01"
    s.increment_daily(day, "ai_scheduled", 3)
    rem = s.remaining_capacity(day)
    # 7 remaining → catch-up
    assert rem["target_remaining"] >= 4


# ── §7 freshness bands ───────────────────────────────

def test_freshness_bands():
    now = datetime.now(timezone.utc)
    s30, bf30, o30 = freshness_score(0.4, now, {})          # 24 min
    assert s30 == config.RANK_FRESHNESS and bf30
    s90, bf90, _ = freshness_score(1.2, now, {})            # 72 min
    assert 0 < s90 < config.RANK_FRESHNESS and bf90
    s5h, _, _ = freshness_score(5, now, {})                 # 5h medium
    assert 0 < s5h < s90
    s20h_dev, _, _ = freshness_score(20, now, {"development_level": 2})   # developing still scores
    s20h_old, _, _ = freshness_score(20, now, {"development_level": 1})   # non-developing → 0
    assert s20h_dev > s20h_old
    _, _, rejected = freshness_score(30, now, {"development_level": 1})   # >24h → reject flag
    assert rejected


def test_old_story_penalized():
    now = datetime.now(timezone.utc)
    fresh = _cl("Trump signs order", age_min=20)
    old = _cl("Trump signs order", cid="old", age_min=20 * 60)  # 20h
    sf, _ = score_cluster(fresh, now)
    so, _ = score_cluster(old, now)
    assert sf > so


# ── §11 geo priorities ───────────────────────────────

def test_us_story_beats_cameroon_local():
    now = datetime.now(timezone.utc)
    us = _cl("Trump announces new White House policy on Iran", cid="us")
    cm = _cl("Cameroon parliament passes local budget in Yaounde", cid="cm")
    su, _ = score_cluster(us, now)
    sc, _ = score_cluster(cm, now)
    assert su > sc
    assert sc < config.MIN_CANDIDATE_SCORE  # filtered out


def test_middle_east_priority():
    now = datetime.now(timezone.utc)
    me = _cl("Israel strikes Gaza after Iranian proxy attack", cid="me")
    generic = _cl("Committee meets to discuss annual report findings", cid="gen")
    sm, _ = score_cluster(me, now)
    sg, _ = score_cluster(generic, now)
    assert sm > sg + 20


def test_russia_ukraine_priority():
    now = datetime.now(timezone.utc)
    ru = _cl("Putin and Zelensky envoys meet in Kyiv as NATO debates", cid="ru")
    generic = _cl("Local council approves parking plan", cid="loc")
    sr, _ = score_cluster(ru, now)
    sl, _ = score_cluster(generic, now)
    assert sr > sl + 20


def test_uk_europe_and_tech_business():
    now = datetime.now(timezone.utc)
    uk = _cl("UK politics: London summit on Europe trade", cid="uk")
    tech = _cl("OpenAI announces new AI model with NVIDIA chips", cid="tech")
    biz = _cl("Stock market rallies as Federal Reserve cuts rates", cid="biz")
    s_uk, _ = score_cluster(uk, now)
    s_tech, _ = score_cluster(tech, now)
    s_biz, _ = score_cluster(biz, now)
    base = _cl("Something neutral happens somewhere today", cid="base")
    s_base, _ = score_cluster(base, now)
    assert s_uk > s_base and s_tech > s_base and s_biz > s_base


# ── §12 candidate scaling + §18 breaking ─────────────

def test_breaking_scheduled_asap():
    now = datetime.now(timezone.utc).replace(hour=12, minute=0)
    stories = [{"story_id": "b", "format": "BREAKING", "is_breaking": True}]
    sched = compute_schedule(stories, existing_scheduled=[], now=now)
    assert len(sched) == 1
    delta = (sched[0]["due_at"] - now).total_seconds() / 60
    assert delta <= config.BREAKING_MAX_DELAY_MINUTES + 0.5  # ASAP, not 1-3h


def test_dynamic_gaps_irregular():
    random.seed(42)
    now = datetime.now(timezone.utc).replace(hour=12, minute=0)
    gaps = {_dynamic_gap(5, now.replace(minute=m)) for m in (0, 7, 13, 21, 33)}
    # Not all identical (irregular), all within bounds
    assert len(gaps) >= 2
    for g in gaps:
        assert config.MIN_NORMAL_GAP_MINUTES <= g <= config.MAX_NORMAL_GAP_MINUTES


def test_multi_post_run_within_quota():
    now = datetime.now(timezone.utc).replace(hour=12, minute=0)
    stories = [{"story_id": f"n{i}", "format": "NEWS_UPDATE", "is_breaking": False} for i in range(4)]
    sched = compute_schedule(stories, existing_scheduled=[], now=now, remaining_quota=4)
    assert len(sched) == 4
    # All distinct times, spaced >= MIN gap
    times = sorted(s["due_at"] for s in sched)
    assert len(set(times)) == 4
    for a, b in zip(times, times[1:]):
        assert (b - a).total_seconds() / 60 >= config.MIN_NORMAL_GAP_MINUTES - 0.5


def test_hard_max_quota_blocks():
    now = datetime.now(timezone.utc).replace(hour=12, minute=0)
    stories = [{"story_id": f"n{i}", "format": "NEWS_UPDATE", "is_breaking": False} for i in range(6)]
    sched = compute_schedule(stories, existing_scheduled=[], now=now, remaining_quota=2)
    assert len(sched) == 2  # quota guard


# ── Candidate pool (§25 catch-up) ────────────────────

def test_candidate_pool_roundtrip():
    import pathlib, tempfile
    from app.database import StateStore
    s = StateStore(pathlib.Path(tempfile.mkdtemp()) / "s.json")
    s.load()
    now = datetime.now(timezone.utc)
    c = _cl("Pool story about Trump and Iran", cid="pool1")
    c["_score"] = 55.0
    c["latest_activity"] = now
    s.update_candidate_pool([c], now)
    pool = s.get_candidate_pool()
    assert len(pool) == 1 and pool[0]["cluster_id"] == "pool1"
    s.mark_pool_posted("pool1")
    assert s.get_candidate_pool() == []
    s.mark_pool_rejected("pool1")  # no-op on missing


# ── §15 hashtags ─────────────────────────────────────

def test_hashtag_rejected():
    ok, _, reason = validate_post("JUST IN: Trump speaks today #Breaking", "NEWS_UPDATE", {"title": "t"})
    assert not ok and "hashtag" in reason
    ok, _, _ = validate_post("JUST IN: Trump speaks about the economy today at length in Washington.", "NEWS_UPDATE", {"title": "t"})
    assert ok
