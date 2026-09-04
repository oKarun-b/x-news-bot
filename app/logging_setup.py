"""Logging setup — single entry point, secret-safe."""
from __future__ import annotations

import logging
import re
from typing import Any

from app import config

_TOKEN_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)
_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", re.IGNORECASE)

_configured = False


class _SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if _TOKEN_RE.search(msg) or _KEY_RE.search(msg):
            record.msg = _TOKEN_RE.sub(r"\1[REDACTED]", str(record.msg))
            record.msg = _KEY_RE.sub(r"\1=[REDACTED]", str(record.msg))
        # also scrub args if present
        if record.args:
            try:
                scrubbed = []
                for a in record.args if isinstance(record.args, tuple) else (record.args,):
                    s = str(a)
                    s = _TOKEN_RE.sub(r"\1[REDACTED]", s)
                    s = _KEY_RE.sub(r"\1=[REDACTED]", s)
                    scrubbed.append(s)
                record.args = tuple(scrubbed) if isinstance(record.args, tuple) else scrubbed[0]
            except Exception:
                pass
        return True


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # Force UTC timestamps in basicConfig format
    logging.Formatter.converter = __import__("time").gmtime  # type: ignore[attr-defined]
    # Attach secret filter to root handlers
    flt = _SecretFilter()
    for h in logging.getLogger().handlers:
        h.addFilter(flt)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    lg = logging.getLogger(name)
    # ensure filter on child loggers' handlers too (in case added later)
    lg.addFilter(_SecretFilter())
    return lg


def run_summary(metrics: dict[str, Any]) -> None:
    """Print a single boxed RUN SUMMARY block. Call once per run."""
    lg = get_logger("x-news-bot.summary")
    lines = ["─" * 48, "RUN SUMMARY"]
    for k, v in metrics.items():
        lines.append(f"  {k}: {v}")
    lines.append("─" * 48)
    lg.info("\n".join(lines))
