import app.normalize as n


def test_canonical_url_strips_tracking():
    url = "https://example.com/a?utm_source=twitter&utm_medium=social&x=1&fbclid=abc"
    assert n.canonical_url(url) == "https://example.com/a?x=1"


def test_canonical_url_lowercases_host():
    assert n.canonical_url("https://Example.COM/Path") == "https://example.com/Path"


def test_canonical_url_removes_fragment():
    assert n.canonical_url("https://example.com/a#section") == "https://example.com/a"


def test_canonical_url_sorts_params():
    assert n.canonical_url("https://example.com/a?z=1&a=2") == "https://example.com/a?a=2&z=1"


def test_normalize_title_strips_suffix_and_punct():
    assert n.normalize_title("Trump faces lawsuit — BBC News") == "trump faces lawsuit"
    assert n.normalize_title("Hello, World!") == "hello world"


def test_normalize_title_lowercase_and_whitespace():
    assert n.normalize_title("  Multiple   Spaces  ") == "multiple spaces"


def test_article_id_deterministic():
    a = n.article_id(None, "https://example.com/a?utm_source=x", "Hello World")
    b = n.article_id(None, "https://example.com/a", "hello world")
    # tracking param stripped, title normalized → same ID
    assert a == b


def test_article_id_prefers_guid():
    g = n.article_id("my-guid-123", "https://example.com/a", "Title")
    # stable across links
    g2 = n.article_id("my-guid-123", "https://example.com/b", "Other")
    assert g == g2


def test_weighted_length_url_counts_23():
    assert n.weighted_length("https://example.com/very/long/path/here") == 23
    assert n.weighted_length("hi https://example.com/a bye") == len("hi ") + 23 + len(" bye")


def test_strip_html():
    assert n.strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert n.strip_html("no tags") == "no tags"


def test_parse_published_rfc2822():
    dt = n.parse_published("Mon, 01 Jan 2024 12:00:00 +0000")
    assert dt is not None
    assert dt.year == 2024


def test_parse_published_iso():
    dt = n.parse_published("2024-01-01T12:00:00Z")
    assert dt is not None
