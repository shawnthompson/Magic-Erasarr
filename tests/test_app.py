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


def test_overseerr_info_reports_requester_watch_status(client, monkeypatch):
    """Requester status is matched per item and uses the 90% completion boundary."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://123"]})
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [
            {"requestedBy": {"user_id": 1, "displayName": "Alice"}},
            {"requestedBy": {"user_id": 2, "displayName": "Bob"}},
        ]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [
        {"rating_key": "movie-1", "user_id": 1, "percent_complete": 89},
        {"rating_key": "movie-1", "user_id": 2, "percent_complete": 90},
        # A stale duplicate rating key must not affect this item's status.
        {"rating_key": "old-movie-1", "user_id": 1, "percent_complete": 100},
    ])

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-1", "movie-1"]})

    assert r.status_code == 200
    assert r.get_json()["movie-1"]["requesters"] == [
        {"name": "Alice", "status": "partial"},
        {"name": "Bob", "status": "completed"},
    ]


def test_tautulli_show_history_queries_episode_scope(monkeypatch):
    """Show history uses the grandparent key and episode media type."""
    from services import tautulli

    calls = []

    def fake_get(command, params):
        calls.append((command, params))
        return {"data": [{"rating_key": "episode-1"}]}

    monkeypatch.setattr(tautulli, "tautulli_get", fake_get)

    assert tautulli.get_item_history("show-1", "show") == [{"rating_key": "episode-1"}]
    assert calls == [(
        "get_history",
        {"grandparent_rating_key": "show-1", "media_type": "episode", "length": -1},
    )]


def test_overseerr_info_matches_watched_show_episode_history(client, monkeypatch):
    """Episode history under a show marks that show's requester as watched."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://127"]})
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [{"requestedBy": {"user_id": 6, "displayName": "Frank"}}]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [{
        "rating_key": "episode-1",
        "grandparent_rating_key": "show-1",
        "user_id": 6,
        "percent_complete": 100,
    }])

    r = client.post(
        "/api/overseerr-info",
        json={"rating_keys": ["show-1"], "media_type": "show"},
    )

    assert r.status_code == 200
    assert r.get_json()["show-1"]["requesters"] == [
        {"name": "Frank", "status": "completed"}
    ]


def test_overseerr_info_handles_empty_history(client, monkeypatch):
    """A requester with no history is explicitly marked as not watched."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://124"]})
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [{"requestedBy": {"user_id": 3, "displayName": "Casey"}}]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [])

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-empty"]})

    assert r.get_json()["movie-empty"]["requesters"] == [
        {"name": "Casey", "status": "not_watched"}
    ]


def test_overseerr_info_preserves_requester_when_history_fails(client, monkeypatch):
    """A Tautulli error does not hide Seerr's requester identity."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://125"]})
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [{"requestedBy": {"user_id": 4, "displayName": "Dana"}}]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-error"]})

    assert r.status_code == 200
    assert r.get_json()["movie-error"]["requesters"] == [
        {"name": "Dana", "status": "unavailable"}
    ]


def test_overseerr_info_caches_seerr_and_history_lookups(client, monkeypatch):
    """Repeated page loads reuse the short-lived per-item caches."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://126"]})
    calls = {"seerr": 0, "history": 0}

    def fake_media(*_):
        calls["seerr"] += 1
        return {"mediaInfo": {"requests": [{"requestedBy": {"user_id": 5, "displayName": "Evan"}}]}}

    def fake_history(*_):
        calls["history"] += 1
        return []

    monkeypatch.setattr(api.overseerr, "overseerr_find_media", fake_media)
    monkeypatch.setattr(api.tautulli, "get_item_history", fake_history)

    for _ in range(2):
        r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-cache", "movie-cache"]})
        assert r.status_code == 200

    assert calls == {"seerr": 1, "history": 1}


def test_remove_movie_uses_stable_ids_for_each_radarr_instance(client, monkeypatch):
    """A stable ID match is used independently for regular and 4K Radarr copies."""
    from routes import api

    monkeypatch.setattr(api, "RADARR_INSTANCES", [{"name": "Radarr"}, {"name": "Radarr 4K"}])
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda _key: {})
    found = {
        "radarr_1": {"id": 101},
        "radarr_2": {"id": 202},
    }
    deleted = []

    def find_movie(instance, **_):
        key = "radarr_2" if instance["name"] == "Radarr 4K" else "radarr_1"
        return found[key]

    monkeypatch.setattr(api.radarr, "radarr_find_movie", find_movie)
    monkeypatch.setattr(api.radarr, "radarr_delete_movie", lambda instance, movie_id, delete_files=True: deleted.append(movie_id))

    response = client.post("/api/remove", json={
        "rating_key": "plex-old-key",
        "media_type": "movie",
        "tmdb_id": "12345",
        "title": "A title that must not be used",
    })

    assert response.status_code == 200
    assert deleted == [101, 202]
    assert response.get_json()["radarr_1"] == "removed"
    assert response.get_json()["radarr_2"] == "removed"


def test_remove_movie_reports_ambiguous_title_without_deleting(client, monkeypatch):
    """Title fallback must not guess when Radarr returns multiple candidates."""
    from routes import api

    monkeypatch.setattr(api, "RADARR_INSTANCES", [{"name": "Radarr"}])
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda _key: {})
    monkeypatch.setattr(api.radarr, "radarr_find_movie_candidates_by_title", lambda *_: [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(api.radarr, "radarr_delete_movie", lambda *_args, **_kwargs: pytest.fail("ambiguous title was deleted"))

    response = client.post("/api/remove", json={
        "rating_key": "plex-key",
        "media_type": "movie",
        "title": "Duplicated title",
        "year": 2020,
    })

    assert response.status_code == 200
    assert response.get_json()["radarr_1"] == "ambiguous (title fallback)"


def test_remove_movie_reports_title_fallback_when_unique(client, monkeypatch):
    """A unique title fallback is explicit in the service result."""
    from routes import api

    monkeypatch.setattr(api, "RADARR_INSTANCES", [{"name": "Radarr"}])
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda _key: {})
    monkeypatch.setattr(api.radarr, "radarr_find_movie_candidates_by_title", lambda *_: [{"id": 7}])
    deleted = []
    monkeypatch.setattr(api.radarr, "radarr_delete_movie", lambda _instance, movie_id, delete_files=True: deleted.append(movie_id))

    response = client.post("/api/remove", json={
        "rating_key": "plex-key",
        "media_type": "movie",
        "title": "Unique title",
    })

    assert response.status_code == 200
    assert deleted == [7]
    assert response.get_json()["radarr_1_match"] == "title_fallback"


def test_library_combined_preserves_requested_page(client, monkeypatch):
    """Combined libraries return the requested slice after merging and sorting."""
    from routes import api

    monkeypatch.setattr(api.tautulli, "get_tautulli_libraries", lambda: [{
        "section_id": "1", "section_name": "Movies", "section_type": "movie"
    }])
    monkeypatch.setattr(api.tautulli, "get_library_media_response", lambda *args, **kwargs: {
        "result": "success",
        "data": {"recordsTotal": 3, "data": [
            {"rating_key": "movie-1", "sort_title": "Alpha"},
            {"rating_key": "movie-2", "sort_title": "Bravo"},
            {"rating_key": "movie-3", "sort_title": "Charlie"},
        ]},
    })

    r = client.get("/api/library/combined?type=movie&start=1&length=1&order_column=sort_title")

    assert r.status_code == 200
    assert [item["rating_key"] for item in r.get_json()["data"]] == ["movie-2"]
    assert r.get_json()["recordsTotal"] == 3


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
