"""
Editorial rules — the ONLY place for writing-style / format guidance.
Brand identity: JUST IN: with 0-2 country flags.
"""
from __future__ import annotations

# ── Urgency scale (internal, not visible) ──────────────
BREAKING = 100
URGENT = 90
HIGH = 75
NORMAL = 50
LOW = 25

# ── Internal editorial formats (NOT visible in final post) ─
# The visible prefix is always JUST IN: (with flags).
# These are internal classifications for prompt intent only.
FORMATS: dict[str, dict] = {
    "BREAKING": {
        "label": "JUST IN:",
        "intent": "A major new event has just happened.",
        "min_urgency": BREAKING,
        "requires_freshness": True,
    },
    "DEVELOPING": {
        "label": "JUST IN:",
        "intent": "A developing story where events are still unfolding.",
        "min_urgency": URGENT,
        "requires_freshness": True,
    },
    "NEWS_UPDATE": {
        "label": "JUST IN:",
        "intent": "A significant confirmed news development.",
        "min_urgency": NORMAL,
        "requires_freshness": False,
    },
    "CONTEXT": {
        "label": "JUST IN:",
        "intent": "Explain why an existing development matters.",
        "min_urgency": LOW,
        "requires_freshness": False,
    },
    "KEY_DETAIL": {
        "label": "JUST IN:",
        "intent": "Highlight one particularly interesting fact.",
        "min_urgency": LOW,
        "requires_freshness": False,
    },
}

# Visible brand prefix — always JUST IN: (flags are prepended programmatically)
BRAND_PREFIX = "JUST IN:"

# Backward compat for tests — all internal formats map to JUST IN:
FORMAT_LABELS = {k: "JUST IN:" for k in FORMATS}

# ── Prohibited visible labels (must not appear in final post) ─
PROHIBITED_LABELS = [
    "NEWS UPDATE",
    "BREAKING NEWS",
    "DEVELOPING",
    "CONTEXT",
    "KEY DETAIL",
]

# ── Style rules ────────────────────────────────────────
STYLE_RULES = """
You write short, readable, news-focused X posts for a fast independent news account.

Brand: Every post starts with JUST IN: (with 0-2 country flags prepended by the app, not you).
Example: 🇺🇸 JUST IN: Trump orders...

Rules:
- Brand is JUST IN: — do not use NEWS UPDATE / BREAKING NEWS / DEVELOPING / CONTEXT / KEY DETAIL as visible labels.
- Country flags (0-2) are added by the app from your country_codes — do not invent flags.
- Lead with the most important verified fact. One concise paragraph by default.
- Second paragraph ONLY if it adds meaningful verified info (consequence, context, quote).
- Natural human newsroom style; no essay-like explanations.
- No generic AI boilerplate: This marks a significant development / This comes amid / sparked widespread debate / major implications / In a major development / Experts say...
- No "You won't believe...", no unnecessary hashtags, no excessive emojis (flags + JUST IN: is enough).
- No invented quotes, no speculation as fact, no unsupported claims.
- No filler, avoid clickbait. Don't force questions or calls to action.
- Vary sentence structure across posts; JUST IN: is constant but what follows must vary.

Post structure — default:
[FLAGS] JUST IN: [MAIN EVENT].

Do not begin with source attribution. BAD: "According to @BBCNews, Trump..."
GOOD: "🇺🇸 JUST IN: Trump orders... @BBCNews reports." or second paragraph attribution.
""".strip()

ATTRIBUTION_RULES = """
Editorial positioning: Independent news aggregation. Never imply original reporting or privileged access.

Attribution policy (owner directive):
- Do NOT include any source names, outlet names, or publisher domains in the post text.
- Do NOT write "according to...", "X reports...", "sources say...", "per Reuters...".
- Do NOT append "- Source" tails. Posts carry the news only; provenance stays internal.
- Never fabricate quotes, statistics, or facts. (Fabrication rules unchanged — we simply don't name sources.)
""".strip()

WRITING_CONSTRAINTS = """
Constraints:
- Target 100-220 characters, hard max 280. Shorter strong posts beat padded weak ones.
- Start with JUST IN: after any flags. The app will prepend flags from your country_codes.
- Example you must produce: "JUST IN: Trump orders..." — the app will add "🇺🇸 " in front to make "🇺🇸 JUST IN:..."
- Do not include flags yourself — return country_codes separately and write post starting with JUST IN:.
- No URLs ever: no http://, https://, www., t.co, bit.ly, article links.
- Plain text only; no markdown.
- 0-2 country flags (app will enforce), 0-2 @mentions (verified only), no more.
- One paragraph by default; optional second paragraph only if adds verified value.
- If no relevant country, use 0 flags: "JUST IN: OpenAI announces..."
""".strip()

MENTION_RULES = """
X mention rules (verified handles only):
- You may use 0-2 @mentions per post, only from the verified registry provided.
- Never invent a handle. If an entity isn't listed, write its plain name.
- Use handles ONLY for the SUBJECT (the person/entity the news is about): "@elonmusk says xAI is preparing..."
- NEVER use a handle for SOURCE attribution: do not write "@BBCNews reports" or "according to @CNN".
- Do NOT add a mention merely because the handle exists. 0 mentions is often correct.
- Distinguish source (who reports — never mention) vs subject (who it's about — mention only if it adds context).
""".strip()

FLAG_RULES = """
Country flag rules:
- Return country_codes as ISO 3166-1 alpha-2 codes: ["US"] or ["US","RU"] or [].
- App converts codes to flag emojis. Do not put flags in the post text yourself.
- 1 flag when story is primarily about one country.
- 2 flags when genuinely bilateral (e.g., US and Russian officials in Moscow).
- 0 flags when global, no clear country, or would be misleading (international institution).
- Maximum 2. Never more. Do not add flags merely because countries are mentioned.
- Flag must correspond to country central to the actual event.
- Examples: 🇺🇸 JUST IN: Trump announces... | 🇺🇸🇷🇺 JUST IN: US and Russian officials begin talks in Moscow. | JUST IN: OpenAI announces...
""".strip()


def build_editorial_prompt(candidates: list[dict]) -> str:
    """Prompt that asks the model to select/reject stories from a compact candidate batch."""
    lines = [
        "You are an editorial selector for an independent X news account (brand: JUST IN:).",
        STYLE_RULES,
        ATTRIBUTION_RULES,
        "Task: from the candidate stories below, identify the most important ones,",
        "whether any represents a genuinely NEW DEVELOPMENT vs prior coverage, and",
        "assign urgency (25/50/75/90/100) and internal format (BREAKING/DEVELOPING/NEWS_UPDATE/CONTEXT/KEY_DETAIL).",
        "Reject weak/old/duplicate stories.",
        "",
        "Return ONLY valid JSON — an array of objects with keys:",
        '  {"story_id": "...", "decision": "select|reject", "urgency": <int>, "format": "BREAKING|DEVELOPING|NEWS_UPDATE|CONTEXT|KEY_DETAIL", "reason": "...", "is_new_development": true|false}',
        "",
        "Candidates:",
    ]
    for c in candidates:
        lines.append(
            f'- id={c.get("story_id")} title="{c.get("title")}" source={c.get("source")} '
            f'published={c.get("published")} category={c.get("category")} '
            f'summary="{(c.get("summary") or "")[:300]}" '
            f'sources={c.get("source_count", 1)} cluster_sources={c.get("cluster_sources", [])}'
            f'{" [PHOTO AVAILABLE]" if c.get("has_image") else ""}'
        )
    lines.append("")
    lines.append("Stories marked [PHOTO AVAILABLE] can be published with a photo — when two stories are editorially comparable, prefer the one with a photo.")
    lines.append("")
    lines.append("Return JSON array only, no prose.")
    return "\n".join(lines)


def build_generation_prompt(story: dict, selected_format: str, mention_context: str = "") -> str:
    """Prompt that asks the model to write the final X post for ONE selected story."""
    # selected_format is internal (BREAKING etc) — intent only, not visible label
    intent = FORMATS.get(selected_format, {}).get('intent', 'A significant news development.')
    parts = [
        "You are a news writer for an independent X account. Brand is JUST IN: (with country flags).",
        STYLE_RULES,
        ATTRIBUTION_RULES,
        WRITING_CONSTRAINTS,
        MENTION_RULES,
        FLAG_RULES,
    ]
    if mention_context:
        parts.append(mention_context)
    parts.extend([
        f"Internal format (for intent only, NOT visible): {selected_format}",
        f"Intent: {intent}",
        "",
        f"Story title: {story.get('title')}",
        f"Source: {story.get('source')}",
        f"Published: {story.get('published')}",
        f"Summary: {(story.get('summary') or '')[:600]}",
        f"Corroborating sources: {story.get('cluster_sources', story.get('source'))}",
        f"Category: {story.get('category')}",
        "",
        "Write the post starting with exactly: JUST IN:",
        "Do not include flags yourself — list country_codes separately.",
        "Return ONLY JSON with all fields:",
        '{"post": "JUST IN: ... (no flags, you will add them)", "country_codes": ["US"], "source": "BBC News", "source_handle": "@BBCNews or null", "subject_handles": [], "story_id": "...", "format": "' + selected_format + '", "confidence": 0.0-1.0}',
        "Rules for JSON:",
        "- post starts with JUST IN: (no flags, no old labels)",
        "- country_codes: [] or [\"US\"] or [\"US\",\"RU\"] (max 2, ISO codes only, use NAME_TO_CODE hints)",
        "- source: plain source name as given",
        "- source_handle: verified handle or null",
        "- subject_handles: 0-2 verified person/entity handles if directly relevant",
        "- No URLs, no invented handles, 100-220 chars preferred, 280 max",
    ])
    return "\n".join(parts)


def build_mention_context(story: dict, recent_handles: list[str] | None = None) -> str:
    """Build the registry + recency hint for the generation prompt."""
    from app.accounts import format_registry_for_prompt, find_handles_for_story

    candidates = find_handles_for_story(story)
    lines = [format_registry_for_prompt()]
    if candidates:
        lines.append(f"\nRelevant handles for this story (use only if natural, 0-2 max): {', '.join(candidates)}")
    else:
        lines.append("\nNo verified handle is clearly relevant for this story — plain name attribution is fine (0 mentions).")
    if recent_handles:
        recent = ", ".join(recent_handles[-5:])
        lines.append(f"Recent mentions (avoid repeating same handle if not essential): {recent}")
    # Add country hint
    try:
        from app.countries import NAME_TO_CODE
        story_text = f"{story.get('title','')} {story.get('summary','')}".lower()
        hints = [f"{name}→{code}" for name, code in NAME_TO_CODE.items() if name in story_text]
        if hints:
            lines.append(f"Country hints for this story: {', '.join(hints[:4])} (convert to ISO codes in country_codes)")
    except Exception:
        pass
    return "\n".join(lines)
