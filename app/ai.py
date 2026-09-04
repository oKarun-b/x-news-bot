"""OpenRouter client — editorial selection + post generation, structured JSON, retries."""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from app import config, editorial
from app.logging_setup import get_logger

log = get_logger("x-news-bot.ai")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── JSON helpers ─────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> Any:
    """Robust JSON extraction: strip fences, find first JSON array/object."""
    if not text:
        raise ValueError("Empty response")
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first [{ or {
    for start in (text.find("["), text.find("{")):
        if start == -1:
            continue
        for end in (text.rfind("]"), text.rfind("}")):
            if end == -1 or end <= start:
                continue
            snippet = text[start: end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Could not parse JSON from: {text[:400]}")


# ── HTTP ─────────────────────────────────────────────

def _chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
) -> str:
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    mdl = model or config.OPENROUTER_MODEL
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/x-news-bot",
        "X-Title": "x-news-bot",
    }
    body = {
        "model": mdl,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "8"))
                log.warning("OpenRouter rate-limited, waiting %ds", wait)
                time.sleep(min(wait, 20))
                continue
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise ValueError(f"No choices in response: {data}")
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                raise ValueError("Empty content in OpenRouter response")
            return content
        except requests.RequestException as exc:
            last_exc = exc
            # Retry transient 5xx / timeout
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status and 500 <= status < 600:
                time.sleep(2 * (attempt + 1))
                continue
            # Network errors: retry once
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except Exception:
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("OpenRouter failed after retries")


# ── Public API ───────────────────────────────────────

def editorial_select(candidates: list[dict]) -> list[dict]:
    """Ask the model to select/reject candidates. Returns parsed array."""
    if not candidates:
        return []
    prompt = editorial.build_editorial_prompt(candidates)
    content = _chat(
        [{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=1500,
    )
    parsed = _extract_json(content)
    if not isinstance(parsed, list):
        raise ValueError(f"Editorial response must be a JSON array, got {type(parsed)}")
    # Validate shape
    out = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if "story_id" not in item or "decision" not in item:
            continue
        out.append(item)
    return out


def generate_post(story: dict, selected_format: str) -> dict:
    """Generate one X post. Returns {post, confidence}."""
    prompt = editorial.build_generation_prompt(story, selected_format)
    content = _chat(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=600,
    )
    parsed = _extract_json(content)
    if not isinstance(parsed, dict) or "post" not in parsed:
        raise ValueError(f"Generation response must be {{\"post\": ...}}, got {parsed}")
    post = str(parsed["post"]).strip()
    if not post:
        raise ValueError("Model returned empty post")
    return {"post": post, "confidence": parsed.get("confidence", 0.8)}
