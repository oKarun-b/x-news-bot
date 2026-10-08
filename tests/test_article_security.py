from unittest.mock import MagicMock, patch

from app import article


def test_rejects_private_and_non_http_destinations():
    for url in (
        "http://127.0.0.1/admin",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/",
        "file:///etc/passwd",
        "https://user:pass@example.com/",
    ):
        assert not article._is_public_http_url(url), url


def test_rejects_hostnames_resolving_to_private_addresses():
    private_result = (None, None, None, None, ("10.1.2.3", 443))
    with patch("app.article.socket.getaddrinfo", return_value=[private_result]):
        assert not article._is_public_http_url("https://news.example/article")


def test_redirect_destination_is_validated_before_request():
    redirect = MagicMock(status_code=302, headers={"Location": "http://127.0.0.1/admin"})
    with patch("app.article._is_public_http_url", side_effect=[True, False]):
        with patch("app.article.requests.get", return_value=redirect) as get:
            try:
                article._get_public_url("https://news.example/article", timeout=5, headers={})
                assert False, "private redirect must be rejected"
            except ValueError as exc:
                assert "not a public" in str(exc)
    get.assert_called_once()
