"""Quality control — pre-Buffer gate. JUST IN brand, flags, mentions, no URL, 280 hard."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app import config, editorial
from app.logging_setup import get_logger
from app.normalize import weighted_length

log = get_logger("x-news-bot.validate")

# ── Hard truncation only as emergency safeguard ──────
def _emergency_truncate(text: str, hard_max: int) -> str:
    if weighted_length(text) <= hard_max:
        return text
    truncated = text[: hard_max - 1].rstrip()
    m = re.search(r".*[.!?]\s", truncated)
    if m:
        cut = m.group(0).rstrip()
        if weighted_length(cut) <= hard_max and len(cut) > hard_max * 0.6:
            return cut
    last_space = truncated.rfind(" ")
    if last_space > hard_max * 0.5:
        truncated = truncated[:last_space].rstrip()
    return truncated + "…"


# ── First-person leak guard (§19: never imply eyewitness/original reporting) ──
_FIRST_PERSON_RE = re.compile(r"\b(my|our|ours|mine|myself|i'm|i've|i am|i was)\b|(?<![A-Za-z])I(?![A-Za-z])", re.IGNORECASE)

def _has_first_person_outside_quotes(text: str) -> bool:
    """Remove quoted segments, then look for first-person pronouns."""
    stripped = re.sub(r'"[^"]*"', " ", text)
    stripped = re.sub(r"\u201c[^\u201d]*\u201d", " ", stripped)
    return bool(_FIRST_PERSON_RE.search(stripped))

# ── Attribution guards (owner directive: no source references in post text) ──
_ACCORDING_TO_RE = re.compile(r"\baccording\s+to\b", re.IGNORECASE)
_SOURCE_CLAIM_RE = re.compile(r"\b(?:sources?\s+(?:say|said|tell|told)|reported\s+by|reports?\s+from)\b", re.IGNORECASE)
# ", The Jerusalem Post reports" / ", BBC reports" — outlet-style attribution tails
_OUTLET_TAIL_RE = re.compile(r"[,;]?\s*(?:the\s+)?[A-Z][\w &.'-]{2,40}\s+(?:reports|reported)\b")
# trailing "- Outlet" / "| Outlet" tails
_DASH_TAIL_RE = re.compile(r"\s*[-\u2013\u2014|]\s*[A-Z][\w &.'-]{2,40}\s*$")
# bare publisher domains without protocol ("yahoo.com", "tribune.net")
_BARE_DOMAIN_RE = re.compile(r"\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|co\.uk|co|io|news)\b", re.IGNORECASE)
_SOURCE_HANDLE_RE = re.compile(r"@(?:BBCNews|BBCWorld|BBCBreaking|Reuters|AFP|AP|guardian|CNN|nytimes|AJEnglish|business|FT|WSJ)\b", re.IGNORECASE)
_SOURCE_NAMES_RE = re.compile(
    r"\b(?:BBC(?:\s+(?:News|World|Breaking))?|Reuters|Associated\s+Press|AP\s+News|AFP|"
    r"The\s+Guardian|Guardian|CNN|The\s+New\s+York\s+Times|New\s+York\s+Times|NYT|"
    r"Al\s+Jazeera|Bloomberg|Financial\s+Times|Wall\s+Street\s+Journal|WSJ)\b",
    re.IGNORECASE,
)

def _find_attribution(text: str) -> str | None:
    if _ACCORDING_TO_RE.search(text) or _SOURCE_CLAIM_RE.search(text):
        return "contains 'according to'"
    if _OUTLET_TAIL_RE.search(text):
        return "contains outlet attribution tail ('... reports')"
    if _BARE_DOMAIN_RE.search(text):
        return "contains bare publisher domain"
    if _SOURCE_HANDLE_RE.search(text):
        return "contains a source handle"
    if _SOURCE_NAMES_RE.search(text):
        return "contains a source name"
    # dash tail only when at the very end (news-title style "- Source")
    if _DASH_TAIL_RE.search(text):
        return "contains '- Source' tail"
    return None


def assert_no_links_or_sources(text: str, source_names: list[str] | None = None) -> None:
    """Last-mile publishing policy guard shared by validation and Buffer client."""
    from app.countries import contains_url
    if contains_url(text):
        raise ValueError("public post text must not contain links or domains")
    reason = _find_attribution(text)
    if not reason:
        for source_name in source_names or []:
            name = str(source_name).strip()
            if len(name) >= 3 and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE):
                reason = "contains a story source name"
                break
    if reason:
        raise ValueError(f"public post text must not contain source attribution ({reason})")

# ── Prohibited visible labels (old brand) ────────────
# Flag old multi-word labels anywhere, and emoji+single-word labels. Don't flag normal words like "context" alone.
_PROHIBITED_RE = re.compile(r"(NEWS UPDATE|BREAKING NEWS|KEY DETAIL|📰\s*NEWS UPDATE|🚨\s*BREAKING NEWS|⚡\s*DEVELOPING|🔎\s*CONTEXT|📌\s*KEY DETAIL)", re.IGNORECASE)

# ── Boilerplate Phrases ──────────────────────────────
_BOILERPLATE_PHRASES = [
    "this marks a significant development",
    "this comes amid",
    "sparked widespread debate",
    "could have major implications",
    "in a major development",
    "experts say this could",
    "this could have major",
    "significant development",
]

def _contains_boilerplate(text: str) -> str | None:
    low = text.lower()
    for phrase in _BOILERPLATE_PHRASES:
        if phrase in low:
            return phrase
    return None


def validate_post(
    post: str,
    selected_format: str,
    story: dict | None = None,
    existing_post_texts: set[str] | None = None,
    ai_generate_fn=None,
) -> tuple[bool, str, str]:
    """
    Validate final post (after flags + mentions inserted).
    Returns (ok, final_post, reason).
    """
    if not post or not post.strip():
        return False, post, "empty post"

    stripped = post.strip()
    from app.content import CAPTION_RE, CLAIM_RE, UNCERTAINTY_RE, story_problem, publication_time, visible_label
    if CAPTION_RE.search(post):
        return False, post, "photo caption is not a news development"
    if story and story.get("require_freshness"):
        problem = story_problem(story.get("title", ""), story.get("published_at"), story.get("validation_time"))
        if problem:
            return False, post, problem
        if CLAIM_RE.search(story.get("title", "")) and not CLAIM_RE.search(post):
            return False, post, "claim attribution lost during generation"
        if UNCERTAINTY_RE.search(story.get("title", "")) and not UNCERTAINTY_RE.search(post):
            return False, post, "uncertainty lost during generation"
        claim = CLAIM_RE.search(story.get("title", ""))
        written_claim = CLAIM_RE.search(post)
        if claim and written_claim:
            from app.images import match_entities
            speakers = {e["slug"] for e in match_entities(story["title"][:claim.start()])}
            written_speakers = {e["slug"] for e in match_entities(post[:written_claim.start()])}
            if speakers and not speakers <= written_speakers:
                return False, post, "named speaker changed during generation"
            if re.search(r"\bden(?:y|ies|ied)\b", claim.group(), re.I) and not re.search(r"\bden(?:y|ies|ied)\b", post, re.I):
                return False, post, "denial lost during generation"

    # ── JUST IN: check (after optional 0-2 flags) ────
    # Flags are 2-char regional indicators, count them
    from app.countries import count_flags, contains_url as _contains_url
    flag_count = count_flags(stripped)
    max_flags = 3 if story and len(set(story.get("country_codes", []))) == 3 else 2
    if flag_count > max_flags:
        return False, post, f"too many flags ({flag_count} > {max_flags})"
    # Remove leading flags + space to check JUST IN:
    without_flags = stripped
    # Flags are at start, each flag is 2 code units but 1 grapheme — our regex finds them
    # Strip them sequentially
    import re as _re
    _flag_re = _re.compile(r"^([\U0001F1E6-\U0001F1FF]{2}\s*)+")
    m = _flag_re.match(without_flags)
    if m:
        without_flags = without_flags[m.end():].lstrip()
    label = next((label for label in ("JUST IN:", "UPDATE:", "CONTEXT:") if without_flags.startswith(label)), None)
    if not label:
        return False, post, "post must start with JUST IN:, UPDATE: or CONTEXT:"
    if story and story.get("require_freshness"):
        if stripped != without_flags:
            return False, post, "flags must follow the label"
        if label != visible_label(selected_format, story.get("published_at"), story.get("validation_time")):
            return False, post, "label does not match publication age or format"
    # Ensure "JUST IN:" is followed by space and content
    after_just_in = _flag_re.sub("", without_flags[len(label):].strip()).strip()
    if not after_just_in or len(after_just_in) < 5:
        return False, post, "JUST IN: must be followed by content"

    # ── Prohibited old labels ────────────────────────
    if _PROHIBITED_RE.search(post):
        return False, post, "contains prohibited visible label (NEWS UPDATE/BREAKING NEWS etc)"

    # ── No URLs ──────────────────────────────────────
    if _contains_url(post):
        return False, post, "post must not contain URLs"

    # ── Breaking freshness ───────────────────────────
    if selected_format in ("BREAKING", "DEVELOPING") and story:
        pub = publication_time(story.get("published_at"))
        if pub is not None:
            try:
                now = story.get("validation_time") or datetime.now(timezone.utc)
                age_min = (now - pub).total_seconds() / 60
                if age_min > config.BREAKING_MAX_AGE_MINUTES:
                    return False, post, f"breaking story too old ({age_min:.0f} min > {config.BREAKING_MAX_AGE_MINUTES})"
            except Exception:
                pass

    # ── Duplicate ────────────────────────────────────
    if existing_post_texts and post.strip() in existing_post_texts:
        return False, post, "duplicate post text"

    wl = weighted_length(post)

    # ── Hard max 280 ─────────────────────────────────
    if wl > 280:
        if ai_generate_fn is not None and wl <= 400:
            try:
                retry = ai_generate_fn(story, selected_format, post, wl)
                if retry:
                    wl2 = weighted_length(retry)
                    if wl2 <= 280:
                        ok, final, reason = validate_post(retry, selected_format, story, existing_post_texts, ai_generate_fn=None)
                        if ok:
                            return True, final, "rewritten"
                        return False, retry, reason
                    # Never publish a truncated claim or bypass the remaining checks.
                    return False, retry, "rewrite exceeds hard limit"
            except Exception as exc:
                log.warning("Rewrite attempt failed: %s", exc)
        return False, post, f"exceeds hard limit {wl} > 280"

    # ── Preferred 100-220 (§9: warn but accept up to hard 280) ──
    if wl < config.PREFERRED_POST_MIN:
        log.warning("Post %d chars below preferred %d-%d — accepting", wl, config.PREFERRED_POST_MIN, config.PREFERRED_POST_MAX)
    elif wl > config.PREFERRED_POST_MAX:
        log.warning("Post %d chars above preferred %d-%d (≤280 allowed) — accepting", wl, config.PREFERRED_POST_MIN, config.PREFERRED_POST_MAX)

    # ── Hashtags (§15: zero by default) ──────────────
    if not config.ALLOW_HASHTAGS:
        _tag_re = re.compile(r"#\w+")
        m = _tag_re.search(post)
        if m:
            return False, post, f"contains hashtag {m.group(0)!r} (disabled by policy)"

    # ── Mention validation ───────────────────────────
    from app.accounts import validate_mentions
    ok_m, reason_m = validate_mentions(post)
    if not ok_m:
        return False, post, f"mention violation: {reason_m}"

    # ── Paragraph check (1-2, no unnecessary second) ─
    paragraphs = [p.strip() for p in post.split("\n\n") if p.strip()]
    # Also handle single newlines as paragraph breaks for lenient check
    if len(paragraphs) > 2:
        return False, post, f"too many paragraphs ({len(paragraphs)} > 2)"
    # If 2 paragraphs, second should not be trivial filler
    if len(paragraphs) == 2 and len(paragraphs[1]) < 15:
        return False, post, "second paragraph too short / filler"

    # ── Boilerplate ──────────────────────────────────
    boiler = _contains_boilerplate(post)
    if boiler:
        return False, post, f"contains AI boilerplate: {boiler!r}"

    # ── First-person leak guard (§19) ────────────────
    if _has_first_person_outside_quotes(post):
        return False, post, "first-person voice outside quotes (must not imply eyewitness/personal account)"

    # ── Attribution guard (owner directive: no source references) ──
    attr = _find_attribution(post)
    if not attr and story:
        source_names = story.get("sources") or story.get("source_names") or []
        if isinstance(source_names, str):
            source_names = [source_names]
        try:
            assert_no_links_or_sources(post, source_names)
        except ValueError as exc:
            attr = str(exc).split("(", 1)[-1].rstrip(")") if "(" in str(exc) else str(exc)
    if attr:
        return False, post, f"attribution violation: {attr}"

    # ── Basic malformed ──────────────────────────────
    if len(post.strip()) < 20:
        return False, post, "post too short"

    return True, post, "ok"


def check_daily_limits(day_counts: dict, kind: str = "ai") -> tuple[bool, str]:
    """Check if daily limits allow another post. kind: 'ai' or 'fixed'."""
    ai_scheduled = day_counts.get("ai_scheduled", 0)
    total = day_counts.get("total", 0)
    if kind == "ai" and ai_scheduled >= config.MAX_AI_POSTS_PER_DAY:
        return False, f"AI daily limit reached ({ai_scheduled}/{config.MAX_AI_POSTS_PER_DAY})"
    if total >= config.MAX_TOTAL_POSTS_PER_DAY:
        return False, f"Total daily limit reached ({total}/{config.MAX_TOTAL_POSTS_PER_DAY})"
    return True, "ok"
