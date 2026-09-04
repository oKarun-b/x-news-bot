from app.filters import dedupe_by_url, dedupe_by_title, dedupe_against_history


def _a(title, canon):
    return {"title": title, "canonical_url": canon, "link": canon}


def test_dedupe_by_url():
    arts = [_a("T1", "https://example.com/a"), _a("T2", "https://example.com/a"), _a("T3", "https://example.com/b")]
    out, dups = dedupe_by_url(arts)
    assert len(out) == 2
    assert dups == 1


def test_dedupe_by_title_normalized():
    arts = [_a("Hello World!", "https://example.com/a"), _a("hello world", "https://example.com/b"), _a("Other", "https://example.com/c")]
    out, dups = dedupe_by_title(arts)
    assert len(out) == 2
    assert dups == 1


def test_dedupe_against_history():
    arts = [_a("New", "https://example.com/new"), _a("Old", "https://example.com/old")]
    seen = {"https://example.com/old"}
    out, dups = dedupe_against_history(arts, seen_canonical=seen)
    assert len(out) == 1
    assert out[0]["canonical_url"] == "https://example.com/new"
