"""Optional article content retrieval — robots-aware, capped, graceful fallback."""
from __future__ import annotations

import re
import ipaddress
import socket
import time
import urllib.robotparser
import urllib.parse

import requests

from app import config
from app.logging_setup import get_logger
from app.normalize import strip_html

log = get_logger("x-news-bot.article")

_REDIRECT_CODES = {301, 302, 303, 307, 308}


def _is_public_http_url(url: str) -> bool:
    """Reject non-web URLs and destinations that could reach local/private networks."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        port = parsed.port  # also validates malformed ports
        if port is not None and not 1 <= port <= 65535:
            return False
        host = parsed.hostname.rstrip(".").lower()
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
                return False
            addresses = [
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            ]
        return bool(addresses) and all(address.is_global for address in addresses)
    except (OSError, ValueError, UnicodeError):
        return False


def _get_public_url(url: str, *, timeout: int, headers: dict) -> requests.Response:
    """GET a public URL while validating each redirect before following it."""
    current = url
    for redirect_count in range(6):
        if not _is_public_http_url(current):
            raise ValueError("URL destination is not a public HTTP(S) address")
        response = requests.get(
            current, timeout=timeout, headers=headers, allow_redirects=False
        )
        if response.status_code not in _REDIRECT_CODES:
            return response
        location = response.headers.get("Location")
        response.close()
        if not location or redirect_count >= 5:
            raise ValueError("Too many or invalid redirects while fetching public URL")
        current = urllib.parse.urljoin(current, location)
    raise ValueError("Too many redirects while fetching public URL")

# crude boilerplate: strip nav/footer-ish lines, keep substantial paragraphs
_BOILER_RE = re.compile(
    r"(subscribe|newsletter|cookie|privacy policy|terms of (use|service)|follow us|share this)",
    re.IGNORECASE,
)


def _allowed_by_robots(url: str, user_agent: str = "x-news-bot") -> bool:
    try:
        if not _is_public_http_url(url):
            return False
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        resp = _get_public_url(robots_url, timeout=5, headers={"User-Agent": user_agent})
        if resp.status_code != 200:
            return True  # no robots.txt → allow
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


# og:image / twitter:image extraction (same HTML fetch as text — no extra request)
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_ALT_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\']',
    re.IGNORECASE,
)


def extract_og_image(html_text: str) -> str | None:
    """Extract og:image / twitter:image URL from HTML. Returns https URL or None."""
    if not html_text:
        return None
    m = _OG_IMAGE_RE.search(html_text) or _ALT_OG_IMAGE_RE.search(html_text)
    if not m:
        return None
    url = m.group(1).strip().replace("&amp;", "&")
    # Only public https images (Buffer requires stable public https URLs)
    if not url.startswith("https://"):
        return None
    if url.lower().startswith(("data:", "blob:")) or "pixel" in url.lower() or "spacer" in url.lower():
        return None
    return url


def fetch_article_text(url: str, max_chars: int = 2500, want_image: bool = False) -> tuple[str | None, str | None]:
    """Fetch and extract article text (and og:image if requested).
    Returns (text, image_url). Either may be None on failure (caller falls back to RSS)."""
    if not url or not _is_public_http_url(url):
        return None, None
    if not _allowed_by_robots(url):
        log.info("Robots disallow: %s", url)
        return None, None
    try:
        resp = _get_public_url(
            url,
            timeout=10,
            headers={"User-Agent": "x-news-bot/1.0", "Accept": "text/html,application/xhtml+xml"},
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype.lower() and "<html" not in resp.text[:2000].lower():
            return None, None
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
        text_out = result if len(result) > 80 else None
        image_out = extract_og_image(resp.text) if (want_image and config.ENABLE_IMAGES and config.ENABLE_ARTICLE_IMAGES) else None
        return text_out, image_out
    except requests.RequestException as exc:
        log.info("Article fetch failed %s: %s", url, exc)
        return None, None
    except Exception as exc:
        log.info("Article extraction failed %s: %s", url, exc)
        return None, None


def enrich_top_candidates(candidates: list[dict], max_fetch: int | None = None) -> None:
    """Mutates candidates in-place, adding 'article_text' and 'og_image' where fetch succeeds."""
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
        text, image = fetch_article_text(link, want_image=True)
        if text:
            c["article_text"] = text
            fetched += 1
            time.sleep(0.5)  # politeness
        if image and "og_image" not in c:
            c["og_image"] = image
