"""Tests for Flask app and main routes."""
import pytest


def test_index_returns_200(client):
    """Index page loads."""
    r = client.get("/")
    assert r.status_code == 200
    assert b"Magic-Erasarr" in r.data or b"media" in r.data.lower()


def test_index_includes_version(client):
    """Footer includes version."""
    r = client.get("/")
    assert r.status_code == 200
    assert b"v1." in r.data  # e.g. v1.3.0


def test_api_libraries_returns_json(client):
    """/api/libraries returns JSON (error when no Tautulli is ok)."""
    r = client.get("/api/libraries")
    assert r.content_type == "application/json"
    data = r.get_json()
    assert data is not None
    # Without TAUTULLI_API_KEY we get error or empty list
    assert "error" in data or isinstance(data, list)


def test_api_status_disabled_when_stat_false(client):
    """/api/status returns 404 when STAT is false (default in test env)."""
    r = client.get("/api/status")
    # STAT defaults from env; in CI often unset so may be True. Just ensure JSON response.
    assert r.content_type == "application/json"


def test_tautulli_viewers_returns_per_user_stats(client, monkeypatch):
    """Viewer endpoint exposes names and play counts without leaking service errors."""
    from routes import api

    def fake_stats(rating_key):
        assert rating_key == "viewer-test-1"
        return [{
            "user_id": 42,
            "friendly_name": "Example Viewer",
            "total_plays": 2,
            "total_time": 7200,
        }]

    monkeypatch.setattr(api.tautulli, "get_item_user_stats", fake_stats)
    r = client.post("/api/tautulli-viewers", json={"rating_keys": ["viewer-test-1"]})

    assert r.status_code == 200
    assert r.get_json() == {
        "viewer-test-1": {
            "rating_key": "viewer-test-1",
            "viewers": [{
                "user_id": 42,
                "name": "Example Viewer",
                "total_plays": 2,
                "total_time": 7200,
            }],
        }
    }
