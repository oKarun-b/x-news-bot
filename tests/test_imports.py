"""Regression: the generate-section imports and dedupe helpers must load (Sep-10 ImportError)."""
import ast
import pathlib


def test_main_generate_section_imports_resolve():
    """Every name imported inside app/main.py must exist in its source module (or be a submodule)."""
    import importlib
    src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = importlib.import_module(node.module)
            for alias in node.names:
                # check the ORIGINAL symbol exists; the local alias can be anything
                if hasattr(mod, alias.name):
                    continue
                # submodule import (e.g. `from app import config`)
                try:
                    importlib.import_module(f"{node.module}.{alias.name}")
                except ImportError:
                    raise AssertionError(
                        f"{node.module} has no attribute {alias.name!r} (imported by app/main.py as {alias.asname or alias.name})"
                    )


def test_dedupe_helpers_work():
    """The cross-run dedupe helpers behave correctly end-to-end."""
    from app.clustering import jaccard, TOKEN_RE
    from app.normalize import normalize_title

    def title_tokens(t):
        return {w for w in TOKEN_RE.findall(normalize_title(t)) if len(w) > 2}

    a = title_tokens("UK government defends ban on trade with Israeli settlements")
    b = title_tokens("UK government defends decision to ban trade with Israeli settlements")
    assert jaccard(a, b) >= 0.5
    c = title_tokens("US strikes Iranian oil tankers in the Gulf of Oman")
    assert jaccard(a, c) < 0.3


def test_main_imports_cleanly():
    import app.main  # noqa: F401 — import-time errors (like the Sep-10 one) fail here
