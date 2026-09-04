"""URL, title, and text normalization — pure functions, easy to test."""
from __future__ import annotations

import base64
import hashlib
import html
import re
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser

# ── Constants ────────────────────────────────────────
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_viz_id", "utm_pu", "utm_referrer",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "igshid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "hsCtaTracking",
    "yclid", "vero_conv", "vero_id", "mkt_tok",
    "ref", "ref_src", "ref_url", "spm", "from", "share_source",
})

# Common publisher suffixes appended to titles (case-insensitive, stripped).
TITLE_SUFFIXES = [
    r"\s*[-–—|]\s*BBC News\s*$",
    r"\s*[-–—|]\s*BBC\s*$",
    r"\s*[-–—|]\s*Reuters\s*$",
    r"\s*[-–—|]\s*The Guardian\s*$",
    r"\s*[-–—|]\s*CNN\s*$",
    r"\s*[-–—|]\s*AP News\s*$",
    r"\s*[-–—|]\s*The New York Times\s*$",
    r"\s*[-–—|]\s*Google News\s*$",
]

_SUFFIX_RES = [re.compile(p, re.IGNORECASE) for p in TITLE_SUFFIXES]

# URL pattern for t.co-weighted counting
_URL_RE = re.compile(r"https?://\S+")

# HTML strip helper
class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    # feedparser already decodes entities sometimes; handle both.
    text = html.unescape(text)
    parser = _Stripper()
    try:
        parser.feed(text)
    except Exception:
        # fallback: crude tag removal
        return re.sub(r"<[^>]+>", "", text).strip()
    return parser.get_text().strip()


# ── URL canonicalization ─────────────────────────────

def _decode_google_news_url(url: str) -> str | None:
    """
    Google News RSS links look like https://news.google.com/rss/articles/...
    The real publisher URL is base64-encoded in the path. Attempt to decode.
    Returns decoded URL or None if not a Google News article URL.
    """
    if "news.google.com/rss/articles/" not in url:
        return None
    # The encoded part is after /articles/ — it's a base64url blob.
    # Buffer of known technique: split, decode, extract embedded http URL.
    # We attempt a best-effort decode looking for http substring.
    try:
        # Extract the b64 segment
        parsed = urllib.parse.urlparse(url)
        # path like /rss/articles/CBMi... or /rss/articles/CBMi...
        # query may contain encoded bits too
        blob = parsed.path.split("/articles/")[-1]
        # Pad base64
        # Try standard and urlsafe
        for variant in (blob, urllib.parse.unquote(blob)):
            # Look for embedded https? The encoded bytes when decoded contain the URL bytes.
            # Strategy: try to base64-decode and scan for http
            padded = variant + "=" * (-len(variant) % 4)
            for b64mod in (base64.urlsafe_b64decode, base64.b64decode):
                try:
                    decoded = b64mod(padded)
                    # decoded is often protobuf-ish; scan for http
                    text = decoded.decode("latin-1", errors="ignore")
                    m = re.search(r"https?://[^\s\x00-\x1f\"'<>]+", text)
                    if m:
                        return m.group(0)
                except Exception:
                    continue
    except Exception:
        pass
    return None


def canonical_url(url: str) -> str:
    """Return a stable canonical URL: decode Google News, strip tracking, normalize."""
    if not url:
        return ""
    url = url.strip()
    # Attempt Google News decode first — if successful, canonicalize the decoded URL instead.
    decoded = _decode_google_news_url(url)
    if decoded:
        url = decoded

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return url

    # Normalize scheme/host
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return url  # malformed, return as-is

    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    # Rebuild path: remove trailing slash unless root
    path = parsed.path or "/"
    # Decode then re-encode path segments to normalize
    # Keep path as-is but strip //, handle.
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    # Filter query params
    qsl = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    filtered = [(k, v) for k, v in qsl if k.lower() not in TRACKING_PARAMS]
    # Sort for determinism
    filtered.sort(key=lambda kv: kv[0])
    query = urllib.parse.urlencode(filtered, doseq=True)

    # Rebuild without fragment
    netloc = f"{host}{port}"
    result = urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))
    return result


# ── Title normalization ──────────────────────────────

def normalize_title(title: str) -> str:
    if not title:
        return ""
    t = html.unescape(title).strip()
    # Strip publisher suffixes
    for rx in _SUFFIX_RES:
        t = rx.sub("", t).strip()
    # Lowercase, remove punctuation, normalize whitespace
    t = t.lower()
    # Replace punctuation with space (keep alphanumerics)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def title_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── Stable IDs ───────────────────────────────────────

def article_id(guid: str | None, link: str, title: str) -> str:
    """
    Deterministic article ID.
    Prefer GUID when it looks stable (non-empty, not a bare URL that will be canonicalized differently).
    Otherwise derive from canonical URL + normalized title.
    """
    if guid and guid.strip():
        g = guid.strip()
        # If guid is a URL, canonicalize it so http/https variants match
        if g.startswith("http"):
            return hashlib.sha256(canonical_url(g).encode()).hexdigest()[:16]
        return hashlib.sha256(g.encode()).hexdigest()[:16]
    # fallback
    canon = canonical_url(link)
    norm = normalize_title(title)
    raw = f"{canon}|{norm}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Character counting (X-weighted) ──────────────────

def weighted_length(text: str) -> int:
    """
    X counts every URL as 23 characters (t.co). We approximate:
    replace each http(s) URL with 23-char placeholder before measuring.
    """
    if not text:
        return 0
    # Replace URLs with 23-char placeholder
    replaced = _URL_RE.sub("x" * 23, text)
    return len(replaced)


# ── Date helpers ─────────────────────────────────────

def parse_published(value: str | None) -> datetime | None:
    """Best-effort parse of RSS published dates. Returns aware UTC datetime or None."""
    if not value:
        return None
    value = value.strip()
    # Try common formats
    fmts = [
        "%a, %d %b %Y %H:%M:%S %z",   # RFC 2822 with tz
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    # Try email.utils
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    return None
