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

def _chat_once(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Single model attempt with internal retries for transient errors."""
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/x-news-bot",
        "X-Title": "x-news-bot",
    }
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    last_exc: Exception | None = None
    for attempt in range(2):  # quick retry, then rotate
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=30)
            if resp.status_code == 429:
                # Don't wait long on shared free tier — rotate quickly
                wait = int(resp.headers.get("Retry-After", "2"))
                log.warning("OpenRouter %s rate-limited (429), rotating quickly (wait %ds)", model, wait)
                time.sleep(min(wait, 3))
                raise RuntimeError(f"Rate-limited 429 for {model}")
            if resp.status_code == 404:
                # Model not found / no free endpoint — don't retry same model
                raise RuntimeError(f"Model {model} not available (404): {resp.text[:300]}")
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise ValueError(f"No choices in response: {data}")
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            # Some reasoning models put answer in reasoning field when content is null
            if not content and msg.get("reasoning"):
                # Try reasoning as fallback only if it looks like JSON/post
                reasoning = msg["reasoning"]
                if "{" in reasoning and "}" in reasoning:
                    log.warning("Model %s returned empty content, using reasoning field", model)
                    content = reasoning
            if not content:
                raise ValueError("Empty content in OpenRouter response")
            return content
        except requests.RequestException as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status and 500 <= status < 600:
                time.sleep(2 * (attempt + 1))
                continue
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except RuntimeError:
            raise
        except Exception:
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError(f"OpenRouter {model} failed after retries")


def _chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
) -> str:
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    primary = model or config.OPENROUTER_MODEL
    candidates = [primary] + [m for m in config.OPENROUTER_FALLBACK_MODELS if m != primary]
    last_err: Exception | None = None
    for mdl in candidates:
        try:
            return _chat_once(messages, mdl, temperature, max_tokens)
        except Exception as exc:
            last_err = exc
            msg = str(exc)
            # Only rotate on model-level failures (404, empty content, 429 exhausted)
            rotatable = any(k in msg for k in ("404", "not available", "Empty content", "rate-limited"))
            if rotatable and mdl != candidates[-1]:
                log.warning("Model %s failed (%s), rotating to next fallback", mdl, msg[:120])
                continue
            # Non-rotatable or last model — propagate
            if mdl == candidates[-1]:
                raise
            # For transient errors already retried inside _chat_once, don't rotate
            raise
    if last_err:
        raise last_err
    raise RuntimeError("OpenRouter failed after trying all models")


# ── Public API ───────────────────────────────────────

def editorial_select(candidates: list[dict]) -> list[dict]:
    """Ask the model to select/reject candidates. Returns parsed array. Rotates on JSON failure."""
    if not candidates:
        return []
    prompt = editorial.build_editorial_prompt(candidates)
    models = [config.OPENROUTER_MODEL] + [m for m in config.OPENROUTER_FALLBACK_MODELS if m != config.OPENROUTER_MODEL]
    last_err: Exception | None = None
    for mdl in models:
        try:
            content = _chat(
                [{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=1500,
                model=mdl,
            )
            parsed = _extract_json(content)
            if not isinstance(parsed, list):
                raise ValueError(f"Editorial response must be a JSON array, got {type(parsed)}")
            out = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                if "story_id" not in item or "decision" not in item:
                    continue
                out.append(item)
            return out
        except Exception as exc:
            last_err = exc
            if mdl != models[-1]:
                log.warning("Editorial %s failed (%s), rotating", mdl, str(exc)[:150])
                continue
            raise
    if last_err:
        raise last_err
    return []


def generate_post(story: dict, selected_format: str) -> dict:
    """Generate one X post. Returns {post, confidence}. Rotates on JSON failure."""
    prompt = editorial.build_generation_prompt(story, selected_format)
    models = [config.OPENROUTER_MODEL] + [m for m in config.OPENROUTER_FALLBACK_MODELS if m != config.OPENROUTER_MODEL]
    last_err: Exception | None = None
    for mdl in models:
        try:
            content = _chat(
                [{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=600,
                model=mdl,
            )
            parsed = _extract_json(content)
            if not isinstance(parsed, dict) or "post" not in parsed:
                raise ValueError(f"Generation response must be {{\"post\": ...}}, got {parsed}")
            post = str(parsed["post"]).strip()
            if not post:
                raise ValueError("Model returned empty post")
            return {"post": post, "confidence": parsed.get("confidence", 0.8)}
        except Exception as exc:
            last_err = exc
            if mdl != models[-1]:
                log.warning("Generate %s failed (%s), rotating", mdl, str(exc)[:150])
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError("Generate failed after trying all models")
