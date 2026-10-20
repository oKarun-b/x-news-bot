"""Deterministic editorial checks shared by intake, drafting and publication."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app import config
from app.normalize import parse_published, normalize_title
from app.clustering import STOPWORDS, jaccard

CAPTION_RE = re.compile(r"\b(?:file[\s-]+photo|file[\s-]+picture|photo\s+caption|pictured\s+(?:above|below)|archive\s+photo|image\s+credit|photo\s+credit)\b", re.I)
CLAIM_RE = re.compile(r"\b(?:says?|said|claims?|claimed|accuses?|accused|warns?|warned|alleges?|alleged|urges?|urged|denies|denied|proposes?|proposed)\b", re.I)
UNCERTAINTY_RE = re.compile(r"\b(?:may|might|could|allegedly|reportedly|possible|possibly|proposed|proposal)\b", re.I)


def clean_headline(title: str, sources: list[str]) -> str:
    """Strip only a known publisher suffix; never discard an arbitrary clause."""
    text = title.strip()
    for source in sorted(sources, key=len, reverse=True):
        if source.strip():
            text = re.sub(r"\s+[-–—|]\s*" + re.escape(source.strip()) + r"\s*$", "", text, flags=re.I)
    return text.strip()


def publication_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            return publication_time(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return parse_published(value)
    return None


def story_problem(title: str, published, now: datetime | None = None) -> str | None:
    if CAPTION_RE.search(title):
        return "photo caption is not a news development"
    date = publication_time(published)
    if date is None:
        return "missing or invalid publication date"
    age = ((now or datetime.now(timezone.utc)) - date).total_seconds() / 60
    if age < -5:
        return "publication date is in the future"
    if age > config.NEWS_MAX_AGE_HOURS * 60:
        return "story too old"
    return None


def visible_label(fmt: str, published, now: datetime | None = None) -> str:
    if fmt in ("CONTEXT", "KEY_DETAIL"):
        return "CONTEXT:"
    date = publication_time(published)
    age = ((now or datetime.now(timezone.utc)) - date).total_seconds() / 60 if date else None
    return "JUST IN:" if age is not None and -5 <= age <= config.BREAKING_MAX_AGE_MINUTES else "UPDATE:"


def render_post(text: str, codes: list[str], fmt: str, published) -> str:
    from app.countries import codes_to_flags
    body = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", text).strip()
    body = re.sub(r"^(?:JUST IN|UPDATE|CONTEXT|NEWS UPDATE|BREAKING NEWS|DEVELOPING|KEY DETAIL)\s*:\s*", "", body, flags=re.I)
    flags = codes_to_flags(codes)
    return f"{visible_label(fmt, published)} {flags + ' ' if flags else ''}{body.strip()}"


def central_country_codes(codes: list[str], title: str) -> list[str]:
    """A third flag requires an explicit country or named leader in the headline."""
    unique = list(dict.fromkeys(codes))
    if len(unique) != 3:
        return unique
    from app.countries import NAME_TO_CODE
    supported = {code for name, code in NAME_TO_CODE.items()
                 if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", title, re.I)}
    leader_codes = {"Trump": "US", "Putin": "RU", "Xi Jinping": "CN", "Zelensky": "UA", "Zelenskyy": "UA", "Macron": "FR", "Starmer": "GB", "Modi": "IN", "Lula": "BR", "Netanyahu": "IL"}
    supported.update(code for name, code in leader_codes.items() if re.search(r"\b" + re.escape(name) + r"\b", title, re.I))
    supported.update(code for code in unique if re.search(r"\b" + re.escape(code) + r"\b", title))
    if not set(unique) <= supported:
        return [code for code in unique if code in supported][:2]
    return unique


def duplicate_development(title: str, previous: str) -> bool:
    """Ignore boilerplate while retaining negation and material numeric changes."""
    def tokens(text):
        return {t for t in normalize_title(text).split() if t not in STOPWORDS and len(t) > 1}
    left, right = tokens(title), tokens(previous)
    if not left or not right:
        return False
    numbers_left, numbers_right = set(re.findall(r"\b\d+(?:\.\d+)?\b", title)), set(re.findall(r"\b\d+(?:\.\d+)?\b", previous))
    if numbers_left and numbers_right and numbers_left != numbers_right:
        return False
    negations = {"not", "no", "denies", "denied", "rejects", "rejected"}
    if left & negations != right & negations:
        return False
    return jaccard(left, right) >= 0.55 or (len(left & right) >= 6 and len(left & right) / min(len(left), len(right)) >= 0.85)
