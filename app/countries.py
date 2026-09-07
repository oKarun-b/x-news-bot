"""
Country → flag mapping and validation.

The AI returns ISO country_codes (e.g. ["US"] or ["US","RU"]),
the app converts them to flag emojis. Never trust the model to emit flags directly.
"""
from __future__ import annotations

import re

# ISO 3166-1 alpha-2 → flag emoji via regional indicator symbols
# Keep this list focused on countries likely to appear in news.
# Add more as needed — the app will reject unknown codes.

_COUNTRY_TO_FLAG: dict[str, str] = {
    "US": "🇺🇸",
    "RU": "🇷🇺",
    "UA": "🇺🇦",
    "CN": "🇨🇳",
    "GB": "🇬🇧",
    "UK": "🇬🇧",
    "DE": "🇩🇪",
    "FR": "🇫🇷",
    "IL": "🇮🇱",
    "PS": "🇵🇸",
    "IR": "🇮🇷",
    "KP": "🇰🇵",
    "KR": "🇰🇷",
    "JP": "🇯🇵",
    "IN": "🇮🇳",
    "BR": "🇧🇷",
    "CA": "🇨🇦",
    "AU": "🇦🇺",
    "TR": "🇹🇷",
    "SA": "🇸🇦",
    "ZA": "🇿🇦",
    "NG": "🇳🇬",
    "MX": "🇲🇽",
    "IT": "🇮🇹",
    "ES": "🇪🇸",
    "PL": "🇵🇱",
    "NL": "🇳🇱",
    "BE": "🇧🇪",
    "CH": "🇨🇭",
    "SE": "🇸🇪",
    "NO": "🇳🇴",
    "DK": "🇩🇰",
    "FI": "🇫🇮",
    "GR": "🇬🇷",
    "PT": "🇵🇹",
    "IE": "🇮🇪",
    "AT": "🇦🇹",
    "CZ": "🇨🇿",
    "HU": "🇭🇺",
    "RO": "🇷🇴",
    "BG": "🇧🇬",
    "RS": "🇷🇸",
    "HR": "🇭🇷",
    "BA": "🇧🇦",
    "AL": "🇦🇱",
    "SY": "🇸🇾",
    "LB": "🇱🇧",
    "JO": "🇯🇴",
    "EG": "🇪🇬",
    "IQ": "🇮🇶",
    "AF": "🇦🇫",
    "PK": "🇵🇰",
    "BD": "🇧🇩",
    "ID": "🇮🇩",
    "MY": "🇲🇾",
    "TH": "🇹🇭",
    "VN": "🇻🇳",
    "PH": "🇵🇭",
    "SG": "🇸🇬",
    "NZ": "🇳🇿",
    "AR": "🇦🇷",
    "CL": "🇨🇱",
    "CO": "🇨🇴",
    "VE": "🇻🇪",
    "PE": "🇵🇪",
    "CU": "🇨🇺",
    "HT": "🇭🇹",
    "DO": "🇩🇴",
    "ET": "🇪🇹",
    "KE": "🇰🇪",
    "GH": "🇬🇭",
    "MA": "🇲🇦",
    "DZ": "🇩🇿",
    "TN": "🇹🇳",
    "LY": "🇱🇾",
    "SD": "🇸🇩",
    "SS": "🇸🇸",
    "SO": "🇸🇴",
    "YE": "🇾🇪",
    "OM": "🇴🇲",
    "AE": "🇦🇪",
    "QA": "🇶🇦",
    "KW": "🇰🇼",
    "BH": "🇧🇭",
    "BY": "🇧🇾",
    "KZ": "🇰🇿",
    "UZ": "🇺🇿",
    "AM": "🇦🇲",
    "AZ": "🇦🇿",
    "GE": "🇬🇪",
    "MD": "🇲🇩",
    "EE": "🇪🇪",
    "LV": "🇱🇻",
    "LT": "🇱🇹",
    "SK": "🇸🇰",
    "SI": "🇸🇮",
    "IS": "🇮🇸",
    "LU": "🇱🇺",
    "MT": "🇲🇹",
    "CY": "🇨🇾",
    "HK": "🇭🇰",
    "TW": "🇹🇼",
    "MO": "🇲🇴",
    "NP": "🇳🇵",
    "LK": "🇱🇰",
    "MM": "🇲🇲",
    "KH": "🇰🇭",
    "LA": "🇱🇦",
    "BN": "🇧🇳",
    "FJ": "🇫🇯",
    "PG": "🇵🇬",
    "BO": "🇧🇴",
    "PY": "🇵🇾",
    "UY": "🇺🇾",
    "EC": "🇪🇨",
    "GT": "🇬🇹",
    "HN": "🇭🇳",
    "SV": "🇸🇻",
    "NI": "🇳🇮",
    "CR": "🇨🇷",
    "PA": "🇵🇦",
    "JM": "🇯🇲",
    "TT": "🇹🇹",
    "BB": "🇧🇧",
    "BS": "🇧🇸",
    "BZ": "🇧🇿",
    "GY": "🇬🇾",
    "SR": "🇸🇷",
}

# Build reverse map flag -> code (for validation)
_FLAG_TO_CODE: dict[str, str] = {v: k for k, v in _COUNTRY_TO_FLAG.items()}

# Regex that matches flag emojis (regional indicator pairs)
_FLAG_RE = re.compile(
    "[\U0001F1E6-\U0001F1FF]{2}"
)

# URL detection for validation
_URL_RE = re.compile(r"https?://|www\.|t\.co|bit\.ly", re.IGNORECASE)


def is_valid_code(code: str) -> bool:
    return code.upper() in _COUNTRY_TO_FLAG


def codes_to_flags(codes: list[str]) -> str:
    """Convert list of ISO codes to concatenated flag emojis. Validates max 2."""
    if not codes:
        return ""
    # Normalize to upper, dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in codes:
        cu = c.strip().upper()
        if cu and cu not in seen:
            seen.add(cu)
            uniq.append(cu)
    if len(uniq) > 2:
        raise ValueError(f"Too many country codes ({len(uniq)} > 2): {uniq}")
    flags = []
    for c in uniq:
        flag = _COUNTRY_TO_FLAG.get(c)
        if not flag:
            raise ValueError(f"Unknown country code: {c!r}")
        flags.append(flag)
    return "".join(flags)


def count_flags(text: str) -> int:
    """Count flag emojis in text."""
    return len(_FLAG_RE.findall(text))


def validate_flags(codes: list[str]) -> tuple[bool, str]:
    if len(codes) > 2:
        return False, f"too many country_codes ({len(codes)} > 2)"
    for c in codes:
        if not is_valid_code(c):
            return False, f"unknown country code {c!r}"
    return True, "ok"


def extract_flags(text: str) -> list[str]:
    return _FLAG_RE.findall(text)


def contains_url(text: str) -> bool:
    return bool(_URL_RE.search(text))


# Convenience: common country name → code for prompt hints
NAME_TO_CODE: dict[str, str] = {
    "united states": "US",
    "usa": "US",
    "america": "US",
    "american": "US",
    "russia": "RU",
    "russian": "RU",
    "ukraine": "UA",
    "ukrainian": "UA",
    "china": "CN",
    "chinese": "CN",
    "united kingdom": "GB",
    "britain": "GB",
    "british": "GB",
    "england": "GB",
    "germany": "DE",
    "german": "DE",
    "france": "FR",
    "french": "FR",
    "israel": "IL",
    "palestine": "PS",
    "palestinian": "PS",
    "iran": "IR",
    "iranian": "IR",
    "north korea": "KP",
    "south korea": "KR",
    "japan": "JP",
    "japanese": "JP",
    "india": "IN",
    "indian": "IN",
    "brazil": "BR",
    "brazilian": "BR",
    "canada": "CA",
    "canadian": "CA",
    "australia": "AU",
    "australian": "AU",
    "turkey": "TR",
    "turkish": "TR",
    "saudi arabia": "SA",
    "saudi": "SA",
}
