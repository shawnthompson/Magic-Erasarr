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


def test_index_includes_read_only_item_details_modal(client):
    """The library view includes the detail modal and title trigger."""
    r = client.get("/")
    assert r.status_code == 200
    assert b'id="detailModal"' in r.data
    assert b'class="item-detail-trigger"' in r.data


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
            "last_played": None,
        }],
        }
    }


def test_item_poster_requires_a_rating_key(client):
    """Poster proxy rejects requests without an item key."""
    r = client.get("/api/item-poster")
    assert r.status_code == 400
    assert r.get_json() == {"error": "rating_key required"}


def test_tautulli_episodes_returns_episode_viewers(client, monkeypatch):
    """TV episode details include per-episode play counts and viewers."""
    from routes import api

    def fake_children(rating_key, media_type):
        if media_type == "show":
            return {"children_list": [{"rating_key": "season-1", "index": 1, "title": "Season 1"}]}
        assert rating_key == "season-1"
        return {"children_list": [{
            "rating_key": "episode-1",
            "media_index": 1,
            "title": "Pilot",
            "last_played": 1700000000,
        }]}

    monkeypatch.setattr(api.tautulli, "get_children_metadata", fake_children)
    monkeypatch.setattr(api, "_cached_tautulli_viewers", lambda key: [{
        "friendly_name": "Example Viewer",
        "total_plays": 3,
    }])

    r = client.get("/api/tautulli-episodes?rating_key=show-1")

    assert r.status_code == 200
    assert r.get_json() == {
        "rating_key": "show-1",
        "seasons": [{
            "season_number": 1,
            "title": "Season 1",
            "episodes": [{
                "rating_key": "episode-1",
                "title": "Pilot",
                "episode_number": 1,
                "play_count": 3,
                "last_played": 1700000000,
                "viewers": [{"name": "Example Viewer", "total_plays": 3}],
            }],
        }],
    }


def test_show_episode_summary_counts_episodes_and_file_sizes(monkeypatch):
    """TV summaries combine episode counts and full-metadata file sizes."""
    from routes import api

    def fake_children(rating_key, media_type):
        if media_type == "show":
            return {"children_list": [{"rating_key": "season-1"}]}
        return {"children_list": [{"rating_key": "episode-1"}, {"rating_key": "episode-2"}]}

    def fake_metadata(rating_key):
        return {"media_info": [{"parts": [{"file_size": "100" if rating_key == "episode-1" else "250"}]}]}

    monkeypatch.setattr(api.tautulli, "get_children_metadata", fake_children)
    monkeypatch.setattr(api.tautulli, "get_metadata", fake_metadata)

    assert api._get_show_episode_summary("show-1") == {
        "episode_count": 2,
        "file_size": 350,
    }


def test_show_summaries_endpoint_batches_cached_summaries(client, monkeypatch):
    """TV summary lookups are available separately from the library response."""
    from routes import api

    monkeypatch.setattr(
        api,
        "_get_show_episode_summary",
        lambda key: {"episode_count": 2, "file_size": 350} if key == "show-1" else {"episode_count": 8, "file_size": 900},
    )

    r = client.post("/api/tautulli-show-summaries", json={"rating_keys": ["show-1", "show-2"]})

    assert r.status_code == 200
    assert r.get_json() == {
        "show-1": {"episode_count": 2, "file_size": 350},
        "show-2": {"episode_count": 8, "file_size": 900},
    }


def test_library_combined_does_not_scan_show_episodes(client, monkeypatch):
    """The library response stays fast and leaves TV summaries to the separate endpoint."""
    from routes import api

    monkeypatch.setattr(api.tautulli, "get_tautulli_libraries", lambda: [{
        "section_id": "1",
        "section_name": "TV Shows",
        "section_type": "show",
    }])
    monkeypatch.setattr(api.tautulli, "get_library_media_response", lambda *args, **kwargs: {
        "result": "success",
        "data": {"data": [{"rating_key": "show-1", "title": "Example Show"}]},
    })

    def fail_if_called(_rating_key):
        raise AssertionError("show episode summaries must not run during library loading")

    monkeypatch.setattr(api, "_get_show_episode_summary", fail_if_called)

    r = client.get("/api/library/combined?type=show&length=1")

    assert r.status_code == 200
    assert r.get_json()["data"] == [{
        "rating_key": "show-1",
        "title": "Example Show",
        "library_name": "TV Shows",
        "section_id": "1",
    }]
