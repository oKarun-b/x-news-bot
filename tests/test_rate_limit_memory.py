"""Tests for daily rate-limit memory (OpenRouter free tier = 50 req/day)."""
from unittest.mock import patch

from app import ai


def test_mark_and_skip_rate_limited():
    ai._rate_limited_models.clear()
    ai.mark_rate_limited("nvidia/test:free")
    assert ai._is_rate_limited_today("nvidia/test:free")
    # _chat should skip the limited model and use fallback
    with patch("app.ai._chat_once", return_value='{"ok": true}') as once:
        with patch.object(ai.config, "OPENROUTER_API_KEY", "k"):
            with patch.object(ai.config, "OPENROUTER_MODEL", "nvidia/test:free"):
                with patch.object(ai.config, "OPENROUTER_FALLBACK_MODELS", ["other/test:free"]):
                    out = ai._chat([{"role": "user", "content": "hi"}])
    assert out == '{"ok": true}'
    # primary skipped, fallback called
    assert once.call_args[0][1] == "other/test:free"


def test_daily_reset_clears_memory():
    ai._rate_limited_models.clear()
    ai.mark_rate_limited("m/a:free")
    # simulate tomorrow
    saved = ai.get_rate_limited_models()
    assert "m/a:free" in saved
    ai._rate_limited_models.clear()
    assert not ai._is_rate_limited_today("m/a:free")


def test_set_rate_limited_ignores_old_dates():
    ai._rate_limited_models.clear()
    ai.set_rate_limited_models({"old/m:free": "2020-01-01"})
    assert not ai._is_rate_limited_today("old/m:free")
    today = ai._utc_today()
    ai.set_rate_limited_models({"new/m:free": today})
    assert ai._is_rate_limited_today("new/m:free")
    ai._rate_limited_models.clear()


def test_all_limited_raises_clean_error():
    ai._rate_limited_models.clear()
    today = ai._utc_today()
    ai._rate_limited_models["a/x:free"] = today
    ai._rate_limited_models["b/y:free"] = today
    with patch.object(ai.config, "OPENROUTER_API_KEY", "k"):
        with patch.object(ai.config, "OPENROUTER_MODEL", "a/x:free"):
            with patch.object(ai.config, "OPENROUTER_FALLBACK_MODELS", ["b/y:free"]):
                try:
                    ai._chat([{"role": "user", "content": "hi"}])
                    assert False, "should raise"
                except RuntimeError as e:
                    assert "daily-limited" in str(e)
    ai._rate_limited_models.clear()
