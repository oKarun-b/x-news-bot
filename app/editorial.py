"""
Editorial rules — the ONLY place for writing-style / format guidance.
Later refined with the owner's sample posts; prompts stay here.
"""
from __future__ import annotations

# ── Urgency scale (spec §10) ─────────────────────────
BREAKING = 100
URGENT = 90
HIGH = 75
NORMAL = 50
LOW = 25

# ── Post formats ─────────────────────────────────────
FORMATS: dict[str, dict] = {
    "BREAKING": {
        "label": "🚨 BREAKING NEWS",
        "emoji": "🚨",
        "intent": "A major new event has just happened.",
        "min_urgency": BREAKING,
        "requires_freshness": True,
    },
    "DEVELOPING": {
        "label": "⚡ DEVELOPING",
        "emoji": "⚡",
        "intent": "A developing story where events are still unfolding.",
        "min_urgency": URGENT,
        "requires_freshness": True,
    },
    "NEWS_UPDATE": {
        "label": "📰 NEWS UPDATE",
        "emoji": "📰",
        "intent": "A significant confirmed news development.",
        "min_urgency": NORMAL,
        "requires_freshness": False,
    },
    "CONTEXT": {
        "label": "🔎 CONTEXT",
        "emoji": "🔎",
        "intent": "Explain why an existing development matters.",
        "min_urgency": LOW,
        "requires_freshness": False,
    },
    "KEY_DETAIL": {
        "label": "📌 KEY DETAIL",
        "emoji": "📌",
        "intent": "Highlight one particularly interesting fact.",
        "min_urgency": LOW,
        "requires_freshness": False,
    },
}

FORMAT_LABELS = {k: v["label"] for k, v in FORMATS.items()}

# ── Style rules (§10, §20, §2) ───────────────────────
STYLE_RULES = """
You write short, readable, news-focused X posts.

Rules:
- Lead with the important fact. Concise sentences.
- Natural human newsroom style; no essay-like explanations.
- No generic AI language, no excessive adjectives, no fake excitement.
- No "You won't believe...", no unnecessary hashtags, no excessive emojis.
- No repetitive hooks or repetitive sentence structures across posts.
- No invented quotes, no speculation presented as fact, no unsupported claims.
- No unnecessary moralizing, no filler, avoid clickbait.
- Posts should be INTERESTING: information + curiosity + clarity (not clickbait+outrage).
- Do not make every post ask a question. Vary openings.
- No excessive emojis: at most the single leading format emoji + one small inline if helpful.
- Keep posts varied across a run; do not reuse the same sentence pattern.
""".strip()

ATTRIBUTION_RULES = """
Editorial positioning: This is an independent X account that discovers, summarizes,
and discusses important current events. It is NOT Reuters/BBC/AP/CNN. Never imply
original reporting, eyewitness, or privileged access.

Attribution guidance:
- Use "According to..." / "Reports say..." / "Reuters reports..." when warranted.
- Do NOT add attribution to every post when information is well established across sources.
- Never fabricate attribution, quotes, statistics, court rulings, or sources.
- Never invent facts.
""".strip()

WRITING_CONSTRAINTS = """
Constraints:
- Target 220-260 characters, never exceed 280.
- Include the given format label prefix exactly as specified.
- Plain text only; no markdown, no extra formatting.
- URLs are not included in post text (Buffer handles links separately if provided).
""".strip()


def build_editorial_prompt(candidates: list[dict]) -> str:
    """Prompt that asks the model to select/reject stories from a compact candidate batch."""
    lines = [
        "You are an editorial selector for an independent X news account.",
        STYLE_RULES,
        ATTRIBUTION_RULES,
        "",
        "Task: from the candidate stories below, identify the most important ones,",
        "whether any represents a genuinely NEW DEVELOPMENT vs prior coverage, and",
        "assign urgency (25/50/75/90/100) and post format.",
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
        )
    lines.append("")
    lines.append("Return JSON array only, no prose.")
    return "\n".join(lines)


def build_generation_prompt(story: dict, selected_format: str) -> str:
    """Prompt that asks the model to write the final X post for ONE selected story."""
    label = FORMAT_LABELS.get(selected_format, selected_format)
    return "\n".join([
        "You are a news writer for an independent X account.",
        STYLE_RULES,
        ATTRIBUTION_RULES,
        WRITING_CONSTRAINTS,
        "",
        f"Write ONE X post in format: {label}",
        f"Format intent: {FORMATS.get(selected_format, {}).get('intent', '')}",
        "",
        f"Story title: {story.get('title')}",
        f"Source: {story.get('source')}",
        f"Published: {story.get('published')}",
        f"Summary: {(story.get('summary') or '')[:600]}",
        f"Corroborating sources: {story.get('cluster_sources', story.get('source'))}",
        f"Category: {story.get('category')}",
        "",
        f"Start the post with exactly: {label}",
        "Then a space, then the post body.",
        "Return ONLY JSON: {\"post\": \"...full post including label...\", \"confidence\": 0.0-1.0}",
    ])
