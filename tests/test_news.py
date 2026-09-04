import types
from unittest.mock import MagicMock, patch

import app.news as news_mod
from app.config import Feed


FAKE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item><title>Story One</title><link>https://example.com/a?utm_source=x</link><guid>g1</guid><description><![CDATA[<p>Summary one</p>]]></description><pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate></item>
<item><title>Story Two</title><link>https://example.com/b</link><guid>g2</guid><description>Summary two</description></item>
<item><title></title><link>https://example.com/c</link></item>
</channel></rss>"""


def _feed(name="Test", url="https://example.com/rss", category="general", tier=1):
    return Feed(name=name, url=url, category=category, tier=tier, enabled=True)


def test_collect_one_feed_ok():
    mock_resp = MagicMock()
    mock_resp.content = FAKE_RSS
    mock_resp.raise_for_status = MagicMock()
    with patch("app.news.requests.get", return_value=mock_resp):
        articles, stats = news_mod.collect_articles([_feed()])
    assert stats["succeeded"] == 1
    assert stats["failed"] == 0
    # empty title entry skipped → 2 articles
    assert len(articles) == 2
    assert articles[0]["id"]
    assert articles[0]["canonical_url"] == "https://example.com/a"
    assert articles[0]["summary"] == "Summary one"


def test_collect_failed_feed_does_not_crash():
    import requests as req
    def fake_get(*a, **kw):
        raise req.ConnectionError("boom")
    feeds = [_feed(name="Bad", url="https://bad.example/rss"), _feed(name="Good", url="https://good.example/rss")]
    # first fails, second succeeds
    good_resp = MagicMock()
    good_resp.content = FAKE_RSS
    good_resp.raise_for_status = MagicMock()
    calls = {"n": 0}
    def side_effect(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise req.ConnectionError("boom")
        return good_resp
    with patch("app.news.requests.get", side_effect=side_effect):
        articles, stats = news_mod.collect_articles(feeds)
    assert stats["failed"] == 1
    assert stats["succeeded"] == 1
    assert len(articles) == 2


def test_collect_malformed_feed_graceful():
    mock_resp = MagicMock()
    mock_resp.content = b"not xml at all <><><"
    mock_resp.raise_for_status = MagicMock()
    with patch("app.news.requests.get", return_value=mock_resp):
        articles, stats = news_mod.collect_articles([_feed()])
    # feedparser returns 0 entries for garbage — still counts as succeeded (HTTP ok), 0 articles
    assert stats["succeeded"] == 1
    assert len(articles) == 0
