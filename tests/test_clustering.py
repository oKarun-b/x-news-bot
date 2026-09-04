from datetime import datetime, timezone

from app.clustering import cluster_articles, jaccard


def _a(title, tier=2, source="Src", pub=None):
    return {
        "id": title[:4] + str(hash(title) % 10000),
        "title": title,
        "source": source,
        "tier": tier,
        "published_at": pub,
        "category": "general",
    }


def test_jaccard():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a"}, {"b"}) == 0.0


def test_same_story_clustered():
    arts = [
        _a("Trump faces new legal challenge", tier=1, source="BBC"),
        _a("Trump hit with fresh legal battle", tier=2, source="Google"),
        _a("New lawsuit targets Trump administration", tier=2, source="Reuters"),
    ]
    clusters = cluster_articles(arts, threshold=0.25)
    # Jaccard(trump faces legal challenge, trump hit fresh legal battle) ≈ 0.285
    # so 0.25 merges the first two; third is more distant — expect at least one merge.
    assert len(clusters) < 3


def test_different_stories_not_clustered():
    arts = [
        _a("NVIDIA unveils new AI chip Blackwell Ultra"),
        _a("Stock market rallies as inflation cools"),
        _a("Ukraine Russia peace talks stall in Geneva"),
    ]
    clusters = cluster_articles(arts)
    assert len(clusters) == 3


def test_identical_titles_cluster():
    arts = [_a("Breaking: Major earthquake hits Tokyo"), _a("Breaking: Major earthquake hits Tokyo")]
    clusters = cluster_articles(arts)
    assert len(clusters) == 1
    assert clusters[0]["size"] == 2


def test_window_prevents_old_merge():
    old = datetime(2024, 1, 1, tzinfo=timezone.utc)
    new = datetime(2024, 1, 10, tzinfo=timezone.utc)
    arts = [_a("Market update: stocks rally", pub=old), _a("Market update: stocks rally", pub=new)]
    clusters = cluster_articles(arts, window_hours=48)
    assert len(clusters) == 2
