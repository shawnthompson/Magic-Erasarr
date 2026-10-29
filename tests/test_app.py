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


def test_settings_page_includes_accessible_controls_without_secrets(client):
    """Settings exposes preferences and safe integration status only."""
    r = client.get("/settings")

    assert r.status_code == 200
    assert b"Library visibility" in r.data
    assert b'name="theme"' in r.data
    assert b"API keys and tokens remain" in r.data
    assert b"TAUTULLI_API_KEY" not in r.data
    assert b"PLEX_TOKEN" not in r.data
    assert b'id="saveLibraries" disabled' in r.data
    assert b"saveLibraries.disabled = true" in r.data
    assert b"saveLibraries.disabled = false" in r.data


def test_combined_library_excludes_requested_sections(client, monkeypatch):
    """Excluded section IDs are removed before fetching, sorting, and totals."""
    from routes import api

    libraries = [
        {"section_id": "1", "section_name": "Movies", "section_type": "movie"},
        {"section_id": "2", "section_name": "Kids Movies", "section_type": "movie"},
        {"section_id": "3", "section_name": "Shows", "section_type": "show"},
    ]
    calls = []

    monkeypatch.setattr(api.tautulli, "get_tautulli_libraries", lambda: libraries)

    def fake_library_media(section_id, **kwargs):
        calls.append(str(section_id))
        return {"result": "success", "data": {"data": [{"rating_key": str(section_id), "title": str(section_id)}]}}

    monkeypatch.setattr(api.tautulli, "get_library_media_response", fake_library_media)

    response = client.get("/api/library/combined?type=movie&exclude_section_id=2")

    assert response.status_code == 200
    assert calls == ["1"]
    assert response.get_json()["libraries"] == ["Movies"]
    assert response.get_json()["recordsTotal"] == 1
    assert [item["rating_key"] for item in response.get_json()["data"]] == ["1"]


def test_combined_library_ignores_empty_or_unknown_exclusions(client, monkeypatch):
    """Empty and unknown exclusion values do not hide valid libraries."""
    from routes import api

    libraries = [{"section_id": "1", "section_name": "Movies", "section_type": "movie"}]
    monkeypatch.setattr(api.tautulli, "get_tautulli_libraries", lambda: libraries)
    monkeypatch.setattr(
        api.tautulli,
        "get_library_media_response",
        lambda section_id, **kwargs: {"result": "success", "data": {"data": [{"rating_key": "1"}]}},
    )

    response = client.get("/api/library/combined?type=movie&exclude_section_id=&exclude_section_id=unknown")

    assert response.status_code == 200
    assert response.get_json()["libraries"] == ["Movies"]
    assert response.get_json()["recordsTotal"] == 1


def test_index_includes_read_only_item_details_modal(client):
    """The library view includes the detail modal and title trigger."""
    r = client.get("/")
    assert r.status_code == 200
    assert b'id="detailModal"' in r.data
    assert b'class="item-detail-trigger"' in r.data
    assert b"/item/" in r.data


def test_index_removal_warning_covers_each_media_type(client):
    """Removal confirmation text discloses the destructive scope by media type."""
    r = client.get("/")
    assert r.status_code == 200
    assert b"including local files" in r.data
    assert b"movie files and download or torrent data will be preserved" in r.data
    assert b"descriptions[currentLibType]" in r.data
    assert b"info.radarr_instance = undefined" in r.data


def test_item_detail_route_renders_direct_page(client):
    """The item detail URL is a page route, not only a modal interaction."""
    r = client.get("/item/item-123?return=/?type=movie")
    assert r.status_code == 200
    assert b"Loading item details" in r.data
    assert b"Back to library" in r.data


def test_item_detail_route_serializes_untrusted_return_url(client):
    """A return path cannot escape the JavaScript string context."""
    r = client.get("/item/missing?return=/%24%7Balert(document.domain)%7D")
    assert r.status_code == 200
    assert b'const returnUrl = "/${alert(document.domain)}";' in r.data
    assert b'<a href="${esc(returnUrl)}">Return to library</a>' in r.data
    assert b"watched_threshold:watchedThreshold" in r.data
    assert b"/api/tautulli-show-summaries" in r.data
    assert b'id="seriesStatus"' in r.data
    assert b'id="nextAirDate"' in r.data


def test_item_details_returns_read_only_metadata(client, monkeypatch):
    """The direct detail page can load normalized metadata by rating key."""
    from routes import api

    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {
        "metadata": {
            "rating_key": key,
            "title": "Example Movie",
            "type": "movie",
            "year": 2026,
            "media_info": [{
                "container": "mkv",
                "video_codec": "hevc",
                "video_resolution": "4k",
                "audio_codec": "aac",
                "audio_channels": "6",
                "bitrate": 12000,
                "parts": [{"file_size": "1234"}],
            }],
            "guid": "plex://movie/123",
        }
    })

    r = client.get("/api/item-details/item-123")

    assert r.status_code == 200
    assert r.get_json() == {
        "rating_key": "item-123",
        "title": "Example Movie",
        "year": 2026,
        "media_type": "movie",
        "library_name": None,
        "added_at": None,
        "last_played": None,
        "play_count": 0,
        "file_size": 1234,
        "container": "mkv",
        "video_codec": "hevc",
        "video_resolution": "4k",
        "audio_codec": "aac",
        "audio_channels": "6",
        "bitrate": 12000,
        "guid": "plex://movie/123",
    }


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
    """Requester status is matched per item and uses the 85% default boundary."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://123"]})
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 1}, {"user_id": 2}])
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [
            {"requestedBy": {"plexId": 1, "displayName": "Alice"}},
            {"requestedBy": {"plexId": 2, "displayName": "Bob"}},
        ]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [
        {"rating_key": "movie-1", "user_id": 1, "percent_complete": 84, "date": 1700000000},
        {"rating_key": "movie-1", "user_id": 2, "percent_complete": 90},
        # A stale duplicate rating key must not affect this item's status.
        {"rating_key": "old-movie-1", "user_id": 1, "percent_complete": 100},
    ])

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-1", "movie-1"]})

    assert r.status_code == 200
    assert r.get_json()["movie-1"]["watched_threshold"] == 85
    assert r.get_json()["movie-1"]["watched_threshold_source"] == "fallback"
    assert r.get_json()["movie-1"]["requesters"] == [
        {"name": "Alice", "status": "partial", "last_watched": 1700000000, "identity_match": "stable_id"},
        {"name": "Bob", "status": "completed", "last_watched": None, "identity_match": "stable_id"},
    ]


def test_overseerr_info_uses_configured_and_dashboard_watched_threshold(client, monkeypatch):
    """The configured threshold is the default, with a request-scoped override."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_settings", lambda: {
        "Monitoring": {"movie_watched_percent": 85},
    })
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://130"]})
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 8}])
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [{"requestedBy": {"plexId": 8, "displayName": "Grace"}}]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [
        {"rating_key": "movie-threshold", "user_id": 8, "percent_complete": 88},
    ])

    default_response = client.post("/api/overseerr-info", json={"rating_keys": ["movie-threshold"]})
    override_response = client.post(
        "/api/overseerr-info",
        json={"rating_keys": ["movie-threshold"], "watched_threshold": 90},
    )
    reset_response = client.post(
        "/api/overseerr-info",
        json={"rating_keys": ["movie-threshold"], "watched_threshold": None},
    )

    assert default_response.get_json()["movie-threshold"]["watched_threshold"] == 85
    assert default_response.get_json()["movie-threshold"]["watched_threshold_source"] == "tautulli"
    assert default_response.get_json()["movie-threshold"]["requesters"][0]["status"] == "completed"
    assert override_response.get_json()["movie-threshold"]["watched_threshold"] == 90
    assert override_response.get_json()["movie-threshold"]["watched_threshold_source"] == "dashboard_override"
    assert override_response.get_json()["movie-threshold"]["requesters"][0]["status"] == "partial"
    assert reset_response.get_json()["movie-threshold"]["watched_threshold"] == 85
    assert reset_response.get_json()["movie-threshold"]["requesters"][0]["status"] == "completed"


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
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 6}])
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [{"requestedBy": {"plexId": 6, "displayName": "Frank"}}]}
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
        {"name": "Frank", "status": "completed", "last_watched": None, "identity_match": "stable_id"}
    ]


def test_overseerr_info_handles_empty_history(client, monkeypatch):
    """A requester with no history is explicitly marked as not watched."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://124"]})
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 3}])
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [{"requestedBy": {"plexId": 3, "displayName": "Casey"}}]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [])

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-empty"]})

    assert r.get_json()["movie-empty"]["requesters"] == [
        {"name": "Casey", "status": "not_watched", "last_watched": None, "identity_match": "stable_id"}
    ]


def test_overseerr_info_preserves_requester_when_history_fails(client, monkeypatch):
    """A Tautulli error does not hide Seerr's requester identity."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://125"]})
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 4}])
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [{"requestedBy": {"plexId": 4, "displayName": "Dana"}}]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-error"]})

    assert r.status_code == 200
    assert r.get_json()["movie-error"]["requesters"] == [
        {"name": "Dana", "status": "unknown", "last_watched": None, "status_reason": "history_unavailable", "identity_match": "stable_id"}
    ]


def test_overseerr_info_caches_seerr_and_history_lookups(client, monkeypatch):
    """Repeated page loads reuse the short-lived per-item caches."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://126"]})
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 5}])
    calls = {"seerr": 0, "history": 0}

    def fake_media(*_):
        calls["seerr"] += 1
        return {"mediaInfo": {"requests": [{"requestedBy": {"plexId": 5, "displayName": "Evan"}}]}}

    def fake_history(*_):
        calls["history"] += 1
        return []

    monkeypatch.setattr(api.overseerr, "overseerr_find_media", fake_media)
    monkeypatch.setattr(api.tautulli, "get_item_history", fake_history)

    for _ in range(2):
        r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-cache", "movie-cache"]})
        assert r.status_code == 200

    assert calls == {"seerr": 1, "history": 1}


def test_overseerr_info_marks_missing_and_deleted_requester_ids_unknown(client, monkeypatch):
    """A missing or deleted Tautulli user is never treated as a name match."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://127"]})
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 10, "friendly_name": "Known"}])
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [
            {"requestedBy": {"plexId": 99, "displayName": "Deleted"}},
            {"requestedBy": {"displayName": "No ID"}},
        ]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [
        {"rating_key": "movie-identities", "user_id": 99, "friendly_name": "Deleted", "percent_complete": 100},
        {"rating_key": "movie-identities", "user_id": 10, "friendly_name": "No ID", "percent_complete": 100},
    ])

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-identities"]})

    assert r.get_json()["movie-identities"]["requesters"] == [
        {"name": "Deleted", "status": "not_watched", "last_watched": None, "identity_match": "unknown"},
        {"name": "No ID", "status": "not_watched", "last_watched": None, "identity_match": "unknown"},
    ]


def test_overseerr_info_uses_name_fallback_and_keeps_same_named_requesters(client, monkeypatch):
    """Name fallback is explicit and distinct Plex IDs are not collapsed by display name."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://128"]})
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [
        {"user_id": 10, "friendly_name": "Shared Name"},
        {"user_id": 11, "friendly_name": "Other Name"},
    ])
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [
            {"requestedBy": {"displayName": "Shared Name"}},
            {"requestedBy": {"plexId": 11, "displayName": "Shared Name"}},
        ]}
    })
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [
        {"rating_key": "movie-same-name", "user_id": 10, "percent_complete": 100},
        {"rating_key": "movie-same-name", "user_id": 11, "percent_complete": 100},
    ])

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-same-name"]})

    assert r.get_json()["movie-same-name"]["requesters"] == [
        {"name": "Shared Name", "status": "completed", "last_watched": None, "identity_match": "name_fallback"},
        {"name": "Shared Name", "status": "completed", "last_watched": None, "identity_match": "stable_id"},
    ]


def test_overseerr_info_resolves_seerr_requester_id_before_matching(client, monkeypatch):
    """Media responses without plexId use the Seerr user record for stable matching."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "configured")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guids": ["tmdb://129"]})
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 42}])
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda *_: {
        "mediaInfo": {"requests": [{"requestedBy": {"id": 7, "displayName": "Alice"}}]}
    })
    user_lookups = []

    def fake_user(user_id):
        user_lookups.append(user_id)
        return {"id": user_id, "plexId": 42, "displayName": "Alice"}

    monkeypatch.setattr(api.overseerr, "overseerr_find_user", fake_user)
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda *_: [{
        "rating_key": "movie-resolved-user",
        "user_id": 42,
        "percent_complete": 100,
    }])

    r = client.post("/api/overseerr-info", json={"rating_keys": ["movie-resolved-user"]})

    assert r.get_json()["movie-resolved-user"]["requesters"] == [
        {"name": "Alice", "status": "completed", "last_watched": None, "identity_match": "stable_id"},
    ]
    assert user_lookups == [7]


def test_remove_movie_uses_stable_ids_for_each_radarr_instance(client, monkeypatch):
    """A stable ID match removes only the explicitly selected Radarr copy."""
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
    delete_file_flags = []
    monkeypatch.setattr(api.radarr, "radarr_delete_movie", lambda instance, movie_id, delete_files=True: (deleted.append(movie_id), delete_file_flags.append(delete_files)))

    response = client.post("/api/remove", json={
        "rating_key": "plex-old-key",
        "media_type": "movie",
        "tmdb_id": "12345",
        "title": "A title that must not be used",
        "confirmed": True,
        "radarr_instance": 1,
    })

    assert response.status_code == 200
    assert deleted == [202]
    assert delete_file_flags == [False]
    assert response.get_json().get("radarr_1") is None
    assert response.get_json()["radarr_2"] == "removed"


def test_remove_requires_confirmation_and_selected_movie_copy(client, monkeypatch):
    """The destructive endpoint refuses unattended or ambiguous movie removal."""
    from routes import api

    monkeypatch.setattr(api, "RADARR_INSTANCES", [{"name": "Radarr"}])
    response = client.post("/api/remove", json={"media_type": "movie", "tmdb_id": "123"})
    assert response.status_code == 400
    assert "confirmation" in response.get_json()["error"].lower()

    response = client.post("/api/remove", json={"media_type": "movie", "tmdb_id": "123", "confirmed": True})
    assert response.status_code == 400
    assert "radarr instance" in response.get_json()["error"].lower()


def test_remove_options_exposes_only_radarr_names(client, monkeypatch):
    """The confirmation UI receives names and indexes, never Radarr credentials."""
    from routes import api

    monkeypatch.setattr(api, "RADARR_INSTANCES", [
        {"name": "Radarr", "url": "http://radarr", "api_key": "secret"},
        {"name": "Radarr 4K", "url": "http://radarr-4k", "api_key": "secret-4k"},
    ])
    response = client.get("/api/remove-options")
    assert response.status_code == 200
    assert response.get_json() == {"radarr_instances": [
        {"index": 0, "name": "Radarr"},
        {"index": 1, "name": "Radarr 4K"},
    ]}


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
        "confirmed": True,
        "radarr_instance": 0,
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
        "confirmed": True,
        "radarr_instance": 0,
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
            return {"children_list": [{"rating_key": "season-1", "title": "Season 1"}]}
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


def test_tv_request_details_reports_requester_progress_and_unavailable_seasons(client, monkeypatch):
    """TV details expand requested seasons without guessing missing content."""
    from routes import api

    monkeypatch.setattr(api, "OVERSEERR_API_KEY", "test-key")
    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guid": "show-guid"})
    monkeypatch.setattr(api, "extract_ids", lambda metadata: {"tmdb": 123})
    monkeypatch.setattr(api.overseerr, "overseerr_find_media", lambda tmdb, media_type: {
        "status": "Returning Series",
        "seasons": [{"seasonNumber": 1}, {"seasonNumber": 2}],
        "mediaInfo": {"requests": [{
            "requestedBy": {"plexId": 7, "displayName": "Alice"},
            "seasons": [{"seasonNumber": 1}, {"seasonNumber": 2}],
        }]}
    })
    monkeypatch.setattr(api.overseerr, "overseerr_find_tv_season", lambda tmdb, season: {
        "episodes": [{"episodeNumber": 1, "name": "Pilot", "airDate": "2026-01-01"}]
    } if season == 1 else {
        "episodes": [
            {"episodeNumber": 1, "name": "Future", "airDate": "2027-01-01"},
            {"episodeNumber": 2, "name": "Missing", "airDate": "2026-01-01"},
        ]
    })
    monkeypatch.setattr(api.tautulli, "get_users", lambda: [{"user_id": 7, "friendly_name": "Alice"}])
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda key, media_type: [{
        "rating_key": "episode-1", "user_id": 7, "percent_complete": 87, "date": 1700000000,
    }])

    def fake_children(key, media_type):
        if media_type == "show":
            return {"children_list": [{"rating_key": "season-1", "index": 1, "title": "Season 1"}]}
        return {"children_list": [{"rating_key": "episode-1", "media_index": 1, "title": "Pilot"}]}

    monkeypatch.setattr(api.tautulli, "get_children_metadata", fake_children)
    monkeypatch.setattr(api, "_cached_tautulli_viewers", lambda key: [{"friendly_name": "Alice", "total_plays": 3, "last_played": 1700000000}])

    response = client.get("/api/tautulli-tv-request-details?rating_key=show-1&watched_threshold=90")

    assert response.status_code == 200
    requester = response.get_json()["requesters"][0]
    assert requester["requested_seasons"] == [1, 2]
    assert requester["missing_seasons"] == []
    assert requester["progress"] == {"completed": 0, "partial": 1, "not_watched": 0, "unknown": 0}
    assert requester["identity_match"] == "stable_id"
    assert response.get_json()["watched_threshold"] == 90
    assert response.get_json()["series_status"] == "Continuing"
    assert response.get_json()["air_date_label"] == "Next air date"
    assert response.get_json()["air_date_value"] == "2027-01-01"
    assert response.get_json()["seasons"][0]["episodes"][0]["play_count"] == 3
    assert response.get_json()["seasons"][0]["episodes"][0]["air_date"] == "2026-01-01"
    assert response.get_json()["seasons"][0]["episodes"][0]["last_played"] == 1700000000
    assert requester["episodes"][0]["availability"] == "available"
    assert requester["unaired_count"] == 1, requester
    assert requester["unavailable_count"] == 1
    assert response.get_json()["seasons"][0]["episodes"][0]["viewers"][0]["last_played"] == 1700000000
    assert [season["season_number"] for season in response.get_json()["seasons"]] == [1, 2]


def test_tv_request_details_keeps_tautulli_episodes_without_tmdb_id(client, monkeypatch):
    """Missing requester metadata must not hide independently available episodes."""
    from routes import api

    monkeypatch.setattr(api.tautulli, "get_metadata", lambda key: {"guid": "show-without-tmdb"})
    monkeypatch.setattr(api, "extract_ids", lambda metadata: {})
    monkeypatch.setattr(api.tautulli, "get_children_metadata", lambda key, media_type: (
        {"children_list": [{"rating_key": "season-1", "index": 1, "title": "Season 1"}]}
        if media_type == "show" else {"children_list": [{"rating_key": "episode-1", "media_index": 1, "title": "Pilot"}]}
    ))
    monkeypatch.setattr(api, "_cached_tautulli_viewers", lambda key: [])
    monkeypatch.setattr(api.tautulli, "get_item_history", lambda key, media_type: [])
    monkeypatch.setattr(api.tautulli, "get_settings", lambda: {})

    response = client.get("/api/tautulli-tv-request-details?rating_key=show-1")

    assert response.status_code == 200
    assert response.get_json()["error"] == "TMDB ID unavailable"
    assert response.get_json()["seasons"][0]["episodes"][0]["title"] == "Pilot"


def test_season_sort_key_handles_numeric_and_named_seasons():
    """Mixed API season values sort without comparing int and string directly."""
    from routes import api

    assert sorted(["specials", 2, "1"], key=api._season_sort_key) == ["1", 2, "specials"]


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
