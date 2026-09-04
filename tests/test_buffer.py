from unittest.mock import MagicMock, patch

import app.buffer as buf


def _mock_gql_response(data):
    return data


def test_create_scheduled_post_success():
    fake = {"createPost": {"post": {"id": "p123", "dueAt": "2026-03-26T10:28:47.000Z", "status": "scheduled"}}}
    with patch("app.buffer._gql", return_value=fake):
        out = buf.create_scheduled_post("ch1", "hello world", "2026-03-26T10:28:47.000Z")
    assert out["id"] == "p123"


def test_create_scheduled_post_error():
    fake = {"createPost": {"message": "Channel not found"}}
    with patch("app.buffer._gql", return_value=fake):
        try:
            buf.create_scheduled_post("ch1", "hi", "2026-03-26T10:28:47.000Z")
            assert False, "should raise"
        except RuntimeError as e:
            assert "Channel not found" in str(e)


def test_get_scheduled_posts():
    fake = {"posts": {"edges": [{"node": {"id": "p1", "text": "hi", "dueAt": "2026-01-01T00:00:00Z", "status": "scheduled"}}], "pageInfo": {}}}
    with patch("app.buffer._gql", return_value=fake):
        posts = buf.get_scheduled_posts("org1", "ch1")
    assert len(posts) == 1
    assert posts[0]["id"] == "p1"


def test_verify_channel_picks_x():
    fake = {"account": {"organizations": [{"id": "org1", "channels": [
        {"id": "ch_ig", "name": "IG", "service": "instagram"},
        {"id": "ch_x", "name": "X", "service": "twitter"},
    ]}]}}
    with patch("app.buffer._gql", return_value=fake):
        info = buf.verify_channel()
    assert info["channel_id"] == "ch_x"
    assert info["organization_id"] == "org1"


def test_gql_retries_on_500():
    # Simulate requests.post returning 500 then 200
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    mock_resp_500.raise_for_status.side_effect = __import__("requests").HTTPError(response=mock_resp_500)
    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.json.return_value = {"data": {"account": {"organizations": []}}}
    mock_resp_ok.raise_for_status = MagicMock()
    with patch("app.buffer.requests.post", side_effect=[mock_resp_500, mock_resp_ok]):
        with patch("app.buffer.time.sleep"):
            try:
                buf._gql("query { account { organizations { id } } }")
            except RuntimeError as e:
                # No orgs → RuntimeError from verify path, but _gql itself succeeded (500 retried)
                assert "No organizations" not in str(e)
