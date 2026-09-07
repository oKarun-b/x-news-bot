from app.countries import codes_to_flags, count_flags, validate_flags, contains_url, is_valid_code


def test_valid_single_flag():
    assert codes_to_flags(["US"]) == "🇺🇸"
    assert codes_to_flags(["us"]) == "🇺🇸"  # case-insensitive


def test_valid_two_flags():
    assert codes_to_flags(["US", "RU"]) == "🇺🇸🇷🇺"
    assert codes_to_flags(["UA", "GB"]) == "🇺🇦🇬🇧"


def test_zero_flags():
    assert codes_to_flags([]) == ""


def test_more_than_two_rejected():
    try:
        codes_to_flags(["US", "RU", "UA"])
        assert False, "should have raised"
    except ValueError as e:
        assert "Too many" in str(e)


def test_unknown_code_rejected():
    try:
        codes_to_flags(["ZZ"])
        assert False
    except ValueError as e:
        assert "Unknown" in str(e)
    ok, reason = validate_flags(["ZZ"])
    assert not ok


def test_count_flags():
    assert count_flags("🇺🇸 JUST IN: hello") == 1
    assert count_flags("🇺🇸🇷🇺 JUST IN: hello") == 2
    assert count_flags("JUST IN: hello") == 0
    assert count_flags("No flags") == 0


def test_contains_url():
    assert contains_url("Check https://example.com")
    assert contains_url("Visit www.example.com")
    assert not contains_url("JUST IN: hello world")


def test_is_valid_code():
    assert is_valid_code("US")
    assert is_valid_code("us")
    assert not is_valid_code("ZZ")
