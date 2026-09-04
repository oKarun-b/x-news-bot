from datetime import datetime, timezone, timedelta

from app.rank import rank_clusters, score_cluster


def _cluster(title, source_count=1, tier=2, size=1, published_at=None, cid="abc123"):
    return {
        "cluster_id": cid,
        "representative_title": title,
        "sources": [f"Src{i}" for i in range(source_count)],
        "source_count": source_count,
        "tier_sum": tier,
        "articles": [{"tier": tier, "category": "world"} for _ in range(size)],
        "size": size,
        "latest_activity": published_at,
        "first_detected": published_at,
    }


def test_recency_fresh_scores_higher():
    now = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    fresh = _cluster("Fresh", published_at=now - timedelta(minutes=30), cid="c1")
    old = _cluster("Old", published_at=now - timedelta(hours=30), cid="c2")
    s_fresh, _ = score_cluster(fresh, now)
    s_old, _ = score_cluster(old, now)
    assert s_fresh > s_old


def test_more_sources_scores_higher():
    now = datetime.now(timezone.utc)
    one = _cluster("One source", source_count=1, cid="c1", published_at=now)
    three = _cluster("Three sources", source_count=3, cid="c2", published_at=now)
    s1, _ = score_cluster(one, now)
    s3, _ = score_cluster(three, now)
    assert s3 > s1


def test_recently_posted_penalized():
    now = datetime.now(timezone.utc)
    c = _cluster("Repeated", cid="repeat", published_at=now)
    state = {"repeat": {"last_posted_at": now.isoformat()}}
    s_no_state, _ = score_cluster(c, now, state_clusters={})
    s_with_penalty, bd = score_cluster(c, now, state_clusters=state)
    assert s_with_penalty < s_no_state
    assert bd["posted_penalty"] < 0


def test_rank_sorts_descending():
    now = datetime.now(timezone.utc)
    clusters = [
        _cluster("Low", source_count=1, cid="low", published_at=now - timedelta(hours=20)),
        _cluster("High", source_count=3, tier=1, cid="high", published_at=now),
    ]
    ranked = rank_clusters(clusters, now)
    assert ranked[0]["cluster_id"] == "high"
