"""Optional article content retrieval — robots-aware, capped, graceful fallback."""
from __future__ import annotations

import re
import time
import urllib.robotparser
import urllib.parse

import requests

from app import config
from app.logging_setup import get_logger
from app.normalize import strip_html

log = get_logger("x-news-bot.article")

# crude boilerplate: strip nav/footer-ish lines, keep substantial paragraphs
_BOILER_RE = re.compile(
    r"(subscribe|newsletter|cookie|privacy policy|terms of (use|service)|follow us|share this)",
    re.IGNORECASE,
)


def _allowed_by_robots(url: str, user_agent: str = "x-news-bot") -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        # timeout via underlying urlopen is not configurable cleanly; use short fetch via requests
        resp = requests.get(robots_url, timeout=5, headers={"User-Agent": user_agent})
        if resp.status_code != 200:
            return True  # no robots.txt → allow
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


def fetch_article_text(url: str, max_chars: int = 2500) -> str | None:
    """Fetch and extract article text. Returns None on any failure (caller falls back to RSS)."""
    if not url or not url.startswith("http"):
        return None
    if not _allowed_by_robots(url):
        log.info("Robots disallow: %s", url)
        return None
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "x-news-bot/1.0", "Accept": "text/html,application/xhtml+xml"},
            timeout=10,
            allow_redirects=True,
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype.lower() and "<html" not in resp.text[:2000].lower():
            return None
        # Extract text
        text = strip_html(resp.text)
        # Remove boilerplate lines
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        kept = []
        for ln in lines:
            if len(ln) < 40:
                continue
            if _BOILER_RE.search(ln):
                continue
            kept.append(ln)
            if sum(len(x) for x in kept) > max_chars:
                break
        result = " ".join(kept)[:max_chars].strip()
        return result if len(result) > 80 else None
    except requests.RequestException as exc:
        log.info("Article fetch failed %s: %s", url, exc)
        return None
    except Exception as exc:
        log.info("Article extraction failed %s: %s", url, exc)
        return None


def enrich_top_candidates(candidates: list[dict], max_fetch: int | None = None) -> None:
    """Mutates candidates in-place, adding 'article_text' where fetch succeeds."""
    limit = max_fetch if max_fetch is not None else config.ARTICLE_FETCH_MAX
    fetched = 0
    for c in candidates:
        if fetched >= limit:
            break
        # candidates are clusters; fetch the representative article link
        rep = c.get("representative_article") or {}
        link = rep.get("link") or rep.get("canonical_url") or ""
        if not link:
            # also check articles list
            arts = c.get("articles") or []
            if arts:
                link = arts[0].get("link", "")
        if not link:
            continue
        text = fetch_article_text(link)
        if text:
            c["article_text"] = text
            fetched += 1
            time.sleep(0.5)  # politeness
