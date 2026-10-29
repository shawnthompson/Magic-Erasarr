"""API routes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import logging
import re
from threading import Lock
import time

import requests
from flask import Blueprint, Response, jsonify, request

from config import (
    DEBUG,
    LIDARR_INSTANCES,
    OVERSEERR_API_KEY,
    OVERSEERR_URL,
    PLEX_TOKEN,
    PLEX_URL,
    RADARR_INSTANCES,
    SONARR_INSTANCES,
    STAT,
)
from services import lidarr, overseerr, plex as plex_svc, radarr, sonarr, tautulli
from utils.ids import extract_ids

api_bp = Blueprint("api", __name__, url_prefix="/api")
logger = logging.getLogger(__name__)

_VIEWER_CACHE_TTL = 300
_viewer_cache = {}
_viewer_cache_lock = Lock()
_REQUESTER_CACHE_TTL = 300
_requester_cache = {}
_requester_cache_lock = Lock()
_REQUESTER_USER_CACHE_TTL = 300
_requester_user_cache = {}
_requester_user_cache_lock = Lock()
_HISTORY_CACHE_TTL = 300
_history_cache = {}
_history_cache_lock = Lock()
_TAUTULLI_USERS_CACHE_TTL = 300
_tautulli_users_cache = None
_tautulli_users_cache_lock = Lock()
_DEFAULT_WATCHED_THRESHOLD = 85
_SUMMARY_CACHE_TTL = 300
_summary_cache = {}
_summary_cache_lock = Lock()


def _metadata_file_size(metadata):
    """Sum file sizes from the parts in a Tautulli metadata response."""
    if not isinstance(metadata, dict):
        return 0
    total = 0
    for media in metadata.get("media_info", []) or []:
        if not isinstance(media, dict):
            continue
        for part in media.get("parts", []) or []:
            if not isinstance(part, dict):
                continue
            try:
                total += int(part.get("file_size") or 0)
            except (TypeError, ValueError):
                pass
    return total


def _fetch_show_episode_summary(rating_key):
    """Return episode count and combined file size for a TV show."""
    show_data = tautulli.get_children_metadata(rating_key, "show")
    episodes = []
    for season in show_data.get("children_list", []) if isinstance(show_data, dict) else []:
        if not isinstance(season, dict) or not season.get("rating_key"):
            continue
        season_data = tautulli.get_children_metadata(season["rating_key"], "season")
        children = season_data.get("children_list", []) if isinstance(season_data, dict) else []
        episodes.extend(
            episode for episode in children
            if isinstance(episode, dict) and episode.get("rating_key")
        )

    def episode_size(episode):
        try:
            return _metadata_file_size(tautulli.get_metadata(episode["rating_key"]))
        except Exception:
            return 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(episode_size, episodes))
    return {"episode_count": len(episodes), "file_size": sum(sizes)}


def _get_show_episode_summary(rating_key):
    """Return a short-lived cached TV episode count and combined file size."""
    key = str(rating_key)
    now = time.monotonic()
    with _summary_cache_lock:
        cached = _summary_cache.get(key)
        if cached and now - cached[0] < _SUMMARY_CACHE_TTL:
            return cached[1]

    summary = _fetch_show_episode_summary(rating_key)
    with _summary_cache_lock:
        _summary_cache[key] = (now, summary)
    return summary


def _unwrap_metadata(data):
    """Normalize the common Tautulli metadata response shapes."""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {}
    for key in ("metadata", "data"):
        nested = data.get(key)
        if isinstance(nested, list):
            return nested[0] if nested and isinstance(nested[0], dict) else {}
        if isinstance(nested, dict):
            data = nested
    return data


def _history_timestamp(row):
    for field in ("date", "started", "stopped", "last_played", "timestamp", "watched_at"):
        value = row.get(field)
        if value in (None, ""):
            continue
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if timestamp > 0:
            return int(timestamp)
    return None


def _completion_percent(row):
    for field in ("percent_complete", "watched_status"):
        value = row.get(field)
        if isinstance(value, str):
            value = value.strip().casefold()
            if value in ("completed", "complete", "watched"):
                return 100
            if value in ("not_watched", "unwatched"):
                return 0
        try:
            return max(0, min(100, float(value)))
        except (TypeError, ValueError):
            continue
    return 0


def _identity_values(user):
    return {
        str(user.get(field) or "").strip().casefold()
        for field in ("displayName", "display_name", "plexUsername", "username", "friendly_name", "email")
        if user.get(field)
    }


@api_bp.route("/item-details/<rating_key>")
def api_item_details(rating_key):
    """Return read-only metadata needed to render a direct item detail page."""
    try:
        metadata = _unwrap_metadata(tautulli.get_metadata(rating_key))
        if not metadata:
            return jsonify({"error": "Item not found"}), 404
        media_info = next(
            (item for item in (metadata.get("media_info") or []) if isinstance(item, dict)),
            {},
        )

        def media_value(field):
            return metadata.get(field) if metadata.get(field) not in (None, "") else media_info.get(field)

        media_type = metadata.get("media_type") or metadata.get("type") or "unknown"
        return jsonify({
            "rating_key": str(metadata.get("rating_key") or rating_key),
            "title": metadata.get("title") or metadata.get("grandparent_title") or "Untitled item",
            "year": metadata.get("year"),
            "media_type": media_type,
            "library_name": metadata.get("library_name") or metadata.get("section_name"),
            "added_at": metadata.get("added_at"),
            "last_played": metadata.get("last_played"),
            "play_count": metadata.get("play_count") or 0,
            "file_size": _metadata_file_size(metadata),
            "container": media_value("container"),
            "video_codec": media_value("video_codec"),
            "video_resolution": media_value("video_resolution"),
            "audio_codec": media_value("audio_codec"),
            "audio_channels": media_value("audio_channels"),
            "bitrate": media_value("bitrate"),
            "guid": metadata.get("guid") or metadata.get("grandparent_guid"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/debug")
def api_debug():
    """Debug: show libraries, a sample item, and its metadata resolution. Only when DEBUG=true in env."""
    if not DEBUG:
        return jsonify({"error": "Debug routes are disabled"}), 404
    out = {}
    try:
        libs = tautulli.get_tautulli_libraries()
        out["libraries"] = [
            {
                "section_id": l.get("section_id"),
                "section_name": l.get("section_name"),
                "section_type": l.get("section_type"),
            }
            for l in libs
        ]
    except Exception as e:
        out["libraries_error"] = str(e)
        return jsonify(out)

    for lib in libs:
        sid = lib.get("section_id")
        try:
            data = tautulli.get_library_media(sid, length=1, start=0)
            items = data.get("data", []) if isinstance(data, dict) else []
            if not items:
                continue
            item = items[0]
            out["sample_library"] = sid
            out["sample_item_keys"] = list(item.keys())
            out["sample_item"] = item

            rk = item.get("rating_key")
            out["rating_key"] = rk
            if rk:
                meta = tautulli.get_metadata(rk)
                out["metadata_from_rating_key"] = meta
                out["extracted_ids"] = extract_ids(meta)
            break
        except Exception as e:
            out[f"library_{sid}_error"] = str(e)

    return jsonify(out)


@api_bp.route("/debug/tautulli-raw-response")
def api_debug_tautulli_raw_response():
    """Return the raw Tautulli get_library_media_info response for the first library of the given type.
    Only when DEBUG=true. Call this while Tautulli is calculating file sizes to see the exact response.
    Query params: type=movie|show|artist (default show).
    """
    if not DEBUG:
        return jsonify({"error": "Debug routes are disabled"}), 404
    section_type = (request.args.get("type") or "show").lower()
    if section_type not in ("movie", "show", "artist"):
        return jsonify({"error": "type must be movie, show, or artist"}), 400
    try:
        libs = tautulli.get_tautulli_libraries()
        libs_of_type = [l for l in (libs or []) if (l.get("section_type") or "").lower() == section_type]
        if not libs_of_type:
            return jsonify({"error": f"No libraries of type {section_type}", "raw_response": None})
        lib = libs_of_type[0]
        sid = lib.get("section_id")
        resp = tautulli.get_library_media_response(
            sid, length=2, start=0, order_column="last_played", order_dir="asc", section_type=section_type
        )
        return jsonify({
            "section_id": sid,
            "section_name": lib.get("section_name"),
            "section_type": section_type,
            "raw_response": resp,
            "result": resp.get("result"),
            "message": resp.get("message"),
            "data_keys": list(resp.get("data", {}).keys()) if isinstance(resp.get("data"), dict) else None,
            "calculating_detected": tautulli.response_indicates_calculating_file_sizes(resp),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/debug/library/<section_id>")
def api_debug_library(section_id):
    """Return raw Tautulli response for a library. Only when DEBUG=true in env."""
    if not DEBUG:
        return jsonify({"error": "Debug routes are disabled"}), 404
    try:
        data = tautulli.get_library_media(section_id, length=2, start=0)
        items = data.get("data", []) if isinstance(data, dict) else []
        section_type = None
        try:
            libs = tautulli.get_tautulli_libraries()
            for lib in (libs or []):
                if str(lib.get("section_id")) == str(section_id):
                    section_type = lib.get("section_type")
                    break
        except Exception:
            pass
        return jsonify({
            "section_id": section_id,
            "section_type": section_type,
            "response_keys": list(data.keys()) if isinstance(data, dict) else [],
            "first_item_keys": list(items[0].keys()) if items else [],
            "first_item": items[0] if items else None,
            "second_item": items[1] if len(items) > 1 else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/status")
def api_status():
    """Connectivity check for all configured services. Only when STAT=true in env.

    Returns a dict keyed by service name.  Each value is either an object
    {"status": "ok", "version": "..."} or a string "error: ...".
    """
    if not STAT:
        return jsonify({"error": "Status endpoint is disabled"}), 404
    result = {}

    try:
        info = tautulli.tautulli_get("get_tautulli_info")
        version = info.get("tautulli_version", "")
        name = (
            info.get("tautulli_product")
            or info.get("product")
            or info.get("app_name")
            or "Tautulli"
        )
        result["tautulli"] = {"status": "ok", "version": version, "name": name}
    except Exception as e:
        result["tautulli"] = f"error: {e}"

    try:
        if not OVERSEERR_API_KEY:
            raise ValueError("API key not set")
        r = requests.get(
            f"{OVERSEERR_URL}/api/v1/status",
            headers=overseerr.overseerr_headers(),
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        version = data.get("version", "")
        name = (
            data.get("applicationTitle")
            or data.get("title")
            or data.get("name")
            or "Seerr"
        )
        result["overseerr"] = {"status": "ok", "version": version, "name": name}
    except Exception as e:
        result["overseerr"] = f"error: {e}"

    for i, inst in enumerate(RADARR_INSTANCES):
        key = f"radarr_{i + 1}"
        try:
            r = requests.get(
                f"{inst['url']}/api/v3/system/status",
                params={"apikey": inst["api_key"]},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            version = data.get("version", "")
            name = (
                data.get("instanceName")
                or data.get("appName")
                or data.get("name")
                or inst["name"]
            )
            result[key] = {"status": "ok", "version": version, "name": name}
        except Exception as e:
            result[key] = f"error: {e}"

    for i, inst in enumerate(SONARR_INSTANCES):
        key = f"sonarr_{i + 1}"
        try:
            r = requests.get(
                f"{inst['url']}/api/v3/system/status",
                params={"apikey": inst["api_key"]},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            version = data.get("version", "")
            name = (
                data.get("instanceName")
                or data.get("appName")
                or data.get("name")
                or inst["name"]
            )
            result[key] = {"status": "ok", "version": version, "name": name}
        except Exception as e:
            result[key] = f"error: {e}"

    for i, inst in enumerate(LIDARR_INSTANCES):
        key = f"lidarr_{i + 1}"
        try:
            r = requests.get(
                f"{inst['url']}/api/v1/system/status",
                params={"apikey": inst["api_key"]},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            version = data.get("version", "")
            name = (
                data.get("instanceName")
                or data.get("appName")
                or data.get("name")
                or inst["name"]
            )
            result[key] = {"status": "ok", "version": version, "name": name}
        except Exception as e:
            result[key] = f"error: {e}"

    return jsonify(result)


@api_bp.route("/instances")
def api_instances():
    """Return the configured instance names so the frontend can render chips."""
    return jsonify({
        "radarr": [
            {"key": f"radarr_{i+1}", "name": inst["name"]}
            for i, inst in enumerate(RADARR_INSTANCES)
        ],
        "sonarr": [
            {"key": f"sonarr_{i+1}", "name": inst["name"]}
            for i, inst in enumerate(SONARR_INSTANCES)
        ],
        "lidarr": [
            {"key": f"lidarr_{i+1}", "name": inst["name"]}
            for i, inst in enumerate(LIDARR_INSTANCES)
        ],
    })


@api_bp.route("/libraries")
def api_libraries():
    """Return all Tautulli libraries (for combined view we only need types)."""
    try:
        libs = tautulli.get_tautulli_libraries()
        return jsonify(libs if isinstance(libs, list) else [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/library/combined")
def api_library_combined():
    """Return media from all libraries of one type (movie/show/artist), merged and sorted.
    Each item includes library_name (Tautulli section_name) and section_id for remove flow.
    """
    section_type = (request.args.get("type") or "movie").lower()
    if section_type not in ("movie", "show", "artist"):
        return jsonify({"error": "type must be movie, show, or artist"}), 400
    length = request.args.get("length", 50, type=int)
    start = request.args.get("start", 0, type=int)
    search = request.args.get("search", "").strip() or None
    library_name_filter = request.args.get("library_name", "").strip() or None
    order_column = request.args.get("order_column", "last_played")
    order_dir = request.args.get("order_dir", "asc")
    allowed_columns = {"sort_title", "year", "added_at", "last_played", "play_count", "file_size", "library_name"}
    if order_column not in allowed_columns:
        order_column = "last_played"
    if order_dir not in ("asc", "desc"):
        order_dir = "asc"
    # Optional: force show the "calculating file sizes" banner for testing (e.g. ?show_calculating_alert=1)
    force_calculating_alert = request.args.get("show_calculating_alert", "").strip() in ("1", "true", "yes")
    try:
        libs = tautulli.get_tautulli_libraries()
        if not isinstance(libs, list):
            libs = []
        # Only libraries of this type
        libs_of_type = [l for l in libs if (l.get("section_type") or "").lower() == section_type]
        if not libs_of_type:
            return jsonify({
                "data": [],
                "recordsFiltered": 0,
                "recordsTotal": 0,
                "section_type": section_type,
                "tautulli_calculating_file_sizes": force_calculating_alert,
            })

        fetch_per_lib = max(length + start, 50)
        all_items = []
        tautulli_calculating_file_sizes = False
        for lib in libs_of_type:
            sid = lib.get("section_id")
            sname = (lib.get("section_name") or "").strip() or "—"
            try:
                resp = tautulli.get_library_media_response(
                    sid,
                    length=fetch_per_lib,
                    start=0,
                    search=search,
                    order_column=order_column,
                    order_dir=order_dir,
                    section_type=section_type,
                )
                if tautulli.response_indicates_calculating_file_sizes(resp):
                    tautulli_calculating_file_sizes = True
                if resp.get("result") != "success":
                    continue
                inner = resp.get("data")
                if isinstance(inner, dict):
                    items = inner.get("data") if isinstance(inner.get("data"), list) else []
                elif isinstance(inner, list):
                    items = inner
                else:
                    items = []
                for item in items:
                    if isinstance(item, dict):
                        item = dict(item)
                        item["library_name"] = sname
                        item["section_id"] = str(sid)
                        all_items.append(item)
            except (requests.RequestException, ValueError, Exception) as e:
                err_str = str(e).lower()
                if "calculating" in err_str and ("file size" in err_str or "file sizes" in err_str or "filesize" in err_str):
                    tautulli_calculating_file_sizes = True
                continue

        if library_name_filter:
            want = library_name_filter.strip().lower()
            all_items = [i for i in all_items if (i.get("library_name") or "").strip().lower() == want]

        # Sort merged list (Tautulli may return timestamps/counts as strings)
        def sort_key(i):
            val = i.get(order_column)
            if order_column == "library_name":
                return (1, (str(val) or "").strip().lower())
            if val is None or val == "":
                return (0, 0) if order_column in ("last_played", "added_at", "play_count", "file_size") else (1, "")
            if order_column in ("last_played", "added_at", "play_count", "file_size"):
                try:
                    n = int(val) if not isinstance(val, (int, float)) else val
                    return (0, n)
                except (TypeError, ValueError):
                    return (1, str(val))
            return (1, (str(val) or "").lower())

        reverse = order_dir == "desc"
        all_items.sort(key=sort_key, reverse=reverse)
        total = len(all_items)
        page_items = all_items[start : start + length]

        out = {
            "data": page_items,
            "recordsFiltered": total,
            "recordsTotal": total,
            "section_type": section_type,
            "libraries": [l.get("section_name") or "" for l in libs_of_type],
            "tautulli_calculating_file_sizes": tautulli_calculating_file_sizes or force_calculating_alert,
        }
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/overseerr-info", methods=["POST"])
def api_overseerr_info():
    """Batch-lookup Seerr requestor info for a list of rating keys.

    Expects JSON:
    {
        "rating_keys": ["123", "456", ...],
        "media_type": "movie" | "show",
        "watched_threshold": 90  # optional, request-scoped percentage override
    }

    Returns a dict keyed by rating_key with requestor info.
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400
    rating_keys = body.get("rating_keys", [])
    if not isinstance(rating_keys, list):
        return jsonify({"error": "rating_keys must be a list"}), 400
    rating_keys = list(dict.fromkeys(str(key) for key in rating_keys if key is not None))[:100]
    media_type = body.get("media_type", "movie")

    def _setting_value(settings, section_name, setting_name):
        if not isinstance(settings, dict):
            return None
        section = next(
            (
                value for key, value in settings.items()
                if str(key).casefold() == section_name.casefold() and isinstance(value, dict)
            ),
            None,
        )
        if not section:
            return None
        value = next(
            (
                value for key, value in section.items()
                if str(key).casefold() == setting_name.casefold()
            ),
            None,
        )
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return int(value) if 0 <= value <= 100 else None

    def _watched_threshold():
        override = body.get("watched_threshold")
        if override not in (None, ""):
            try:
                override = float(override)
            except (TypeError, ValueError):
                override = None
            if override is not None and 0 <= override <= 100:
                return int(override), "dashboard_override"
        try:
            configured = _setting_value(
                tautulli.get_settings(), "Monitoring", "movie_watched_percent"
            )
        except Exception:
            configured = None
        if configured is not None:
            return configured, "tautulli"
        return _DEFAULT_WATCHED_THRESHOLD, "fallback"

    if not OVERSEERR_API_KEY:
        return jsonify({})

    watched_threshold, watched_threshold_source = _watched_threshold()

    def _cached_requester_media(tmdb_id):
        key = (str(tmdb_id), str(media_type))
        now = time.monotonic()
        with _requester_cache_lock:
            cached = _requester_cache.get(key)
            if cached and now - cached[0] < _REQUESTER_CACHE_TTL:
                return cached[1]
        media = overseerr.overseerr_find_media(tmdb_id, media_type)
        with _requester_cache_lock:
            _requester_cache[key] = (now, media)
        return media

    def _cached_item_history(rk):
        key = (str(rk), str(media_type))
        now = time.monotonic()
        with _history_cache_lock:
            cached = _history_cache.get(key)
            if cached and now - cached[0] < _HISTORY_CACHE_TTL:
                return cached[1]
        history = tautulli.get_item_history(rk, media_type)
        with _history_cache_lock:
            _history_cache[key] = (now, history)
        return history

    def _cached_requester_user(user_id):
        key = str(user_id)
        now = time.monotonic()
        with _requester_user_cache_lock:
            cached = _requester_user_cache.get(key)
            if cached and now - cached[0] < _REQUESTER_USER_CACHE_TTL:
                return cached[1]
        try:
            user = overseerr.overseerr_find_user(user_id)
        except Exception:
            user = None
        with _requester_user_cache_lock:
            _requester_user_cache[key] = (now, user)
        return user

    def _cached_tautulli_users():
        global _tautulli_users_cache
        now = time.monotonic()
        with _tautulli_users_cache_lock:
            cached = _tautulli_users_cache
            if cached and now - cached[0] < _TAUTULLI_USERS_CACHE_TTL:
                return cached[1]
        try:
            users = tautulli.get_users()
        except Exception:
            # Requester names from Seerr remain useful even when Tautulli's
            # user list is temporarily unavailable; identity is then unknown.
            return []
        users = [user for user in users if isinstance(user, dict)]
        with _tautulli_users_cache_lock:
            _tautulli_users_cache = (now, users)
        return users

    def _identity_values(user):
        values = {
            str(user.get(field) or "").strip().casefold()
            for field in (
                "displayName", "display_name", "plexUsername", "username",
                "friendly_name", "email",
            )
        }
        return {value for value in values if value}

    def _history_matches_requester(row, user_id, fallback_names):
        if not isinstance(row, dict):
            return False
        history_id = row.get("user_id")
        if user_id is not None:
            return history_id is not None and str(user_id) == str(history_id)
        history_names = {
            str(row.get(field) or "").strip().casefold()
            for field in ("friendly_name", "user", "username", "display_name")
        }
        return bool(history_names & fallback_names)

    def _history_timestamp(row):
        """Return the best available watched timestamp from a history row."""
        for field in ("date", "started", "stopped", "last_played", "timestamp", "watched_at"):
            value = row.get(field)
            if value in (None, ""):
                continue
            try:
                timestamp = float(value)
            except (TypeError, ValueError):
                continue
            if timestamp > 0:
                return int(timestamp)
        return None

    def _completion_percent(row):
        for field in ("percent_complete", "watched_status"):
            value = row.get(field)
            if value in (None, ""):
                continue
            if isinstance(value, str):
                value = value.strip().casefold()
                if value in ("completed", "complete", "watched"):
                    return 100
                if value in ("not_watched", "unwatched"):
                    return 0
            try:
                return max(0, min(100, float(value)))
            except (TypeError, ValueError):
                continue
        return 0

    def _requester_status(user_id, fallback_names, history, rating_key):
        matching = [
            row for row in history
            if isinstance(row, dict)
            and (
                not row.get("rating_key")
                or str(row.get("rating_key")) == str(rating_key)
                or (
                    media_type == "show"
                    and str(row.get("grandparent_rating_key")) == str(rating_key)
                )
            )
            and _history_matches_requester(row, user_id, fallback_names)
        ]
        if not matching:
            return {"status": "not_watched", "last_watched": None}
        highest = max(_completion_percent(row) for row in matching)
        timestamps = [_history_timestamp(row) for row in matching]
        timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
        return {
            "status": "completed" if highest >= watched_threshold else "partial",
            "last_watched": max(timestamps) if timestamps else None,
        }

    def _lookup(rk):
        result = {
            "rating_key": rk,
            "requested_by": None,
            "requesters": [],
            "watched_threshold": watched_threshold,
            "watched_threshold_source": watched_threshold_source,
        }
        try:
            meta = tautulli.get_metadata(rk)
            ids = extract_ids(meta)
            tmdb_id = ids.get("tmdb")
            if not tmdb_id:
                return result

            media = _cached_requester_media(tmdb_id)
            if not media:
                return result

            media_info = media.get("mediaInfo")
            if not media_info:
                return result

            reqs = media_info.get("requests") or []
            requestors = []
            requester_users = []
            requester_keys = set()
            tautulli_users = _cached_tautulli_users()
            tautulli_by_id = {
                str(user.get("user_id")): user
                for user in tautulli_users
                if user.get("user_id") is not None
            }
            stable_matches = fallback_matches = unknown_matches = 0
            for req in reqs:
                if not isinstance(req, dict):
                    continue
                user = req.get("requestedBy") or {}
                seerr_name = (
                    user.get("displayName")
                    or user.get("plexUsername")
                    or user.get("email")
                    or None
                )
                resolved_user = user
                if user.get("plexId") is None and user.get("id") is not None:
                    resolved_user = _cached_requester_user(user["id"]) or user
                plex_id = resolved_user.get("plexId")
                tautulli_user = tautulli_by_id.get(str(plex_id)) if plex_id is not None else None
                fallback_names = _identity_values(resolved_user)
                if tautulli_user:
                    match_type = "stable_id"
                    name = seerr_name or next(
                        (tautulli_user.get(field) for field in ("friendly_name", "username") if tautulli_user.get(field)),
                        None,
                    )
                    user_id = tautulli_user.get("user_id")
                    stable_matches += 1
                elif plex_id is None:
                    name_match = next(
                        (
                            candidate for candidate in tautulli_users
                            if _identity_values(candidate) & fallback_names
                        ),
                        None,
                    )
                    match_type = "name_fallback" if name_match else "unknown"
                    name = seerr_name or (
                        next(
                            (name_match.get(field) for field in ("friendly_name", "username") if name_match.get(field)),
                            None,
                        )
                        if name_match else None
                    )
                    user_id = name_match.get("user_id") if name_match else None
                    if name_match:
                        fallback_matches += 1
                    else:
                        unknown_matches += 1
                else:
                    match_type = "unknown"
                    name = seerr_name
                    user_id = None
                    unknown_matches += 1
                status_names = fallback_names if match_type == "name_fallback" else set()
                identity_key = (
                    ("id", str(user_id))
                    if user_id is not None
                    else ("name", (name or "").casefold())
                )
                if name and identity_key not in requester_keys:
                    requester_keys.add(identity_key)
                    requestors.append(name)
                    requester_users.append((name, user_id, status_names, match_type))
            if requestors:
                try:
                    history = _cached_item_history(rk)
                    history_error = False
                except Exception:
                    history = []
                    history_error = True
                result["requesters"] = [
                    {
                        "name": name,
                        **(
                            {
                                "status": "unknown",
                                "last_watched": None,
                                "status_reason": "history_unavailable",
                            }
                            if history_error
                            else _requester_status(user_id, fallback_names, history, rk)
                        ),
                        "identity_match": match_type,
                    }
                    for name, user_id, fallback_names, match_type in requester_users
                ]
                result["requested_by"] = ", ".join(requestors)
            logger.debug(
                "Requester identity resolution: stable_id=%d name_fallback=%d unknown=%d",
                stable_matches,
                fallback_matches,
                unknown_matches,
            )
        except Exception:
            pass
        return result

    try:
        info = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_lookup, rk): rk for rk in rating_keys}
            for fut in as_completed(futures):
                res = fut.result()
                info[res["rating_key"]] = res
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _cached_tautulli_viewers(rating_key):
    """Return cached per-user item stats, refreshing at most every five minutes."""
    key = str(rating_key)
    now = time.monotonic()
    with _viewer_cache_lock:
        cached = _viewer_cache.get(key)
        if cached and now - cached[0] < _VIEWER_CACHE_TTL:
            return cached[1]

    viewers = tautulli.get_item_user_stats(rating_key)
    with _viewer_cache_lock:
        _viewer_cache[key] = (now, viewers)
    return viewers


@api_bp.route("/tautulli-viewers", methods=["POST"])
def api_tautulli_viewers():
    """Batch-return aggregate Tautulli play statistics by item and user.

    Expects JSON: {"rating_keys": ["123", "456", ...]}
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400

    raw_keys = body.get("rating_keys", [])
    if not isinstance(raw_keys, list):
        return jsonify({"error": "rating_keys must be a list"}), 400
    rating_keys = list(dict.fromkeys(str(key) for key in raw_keys if key is not None))[:100]

    def _lookup(rating_key):
        result = {"rating_key": rating_key, "viewers": []}
        try:
            for row in _cached_tautulli_viewers(rating_key):
                if not isinstance(row, dict):
                    continue
                name = row.get("friendly_name") or row.get("username")
                if not name:
                    continue
                result["viewers"].append({
                    "user_id": row.get("user_id"),
                    "name": str(name),
                    "total_plays": row.get("total_plays", 0),
                    "total_time": row.get("total_time", 0),
                    "last_played": row.get("last_played") or row.get("last_watched"),
                })
        except Exception:
            # Do not expose service errors or credentials through the API.
            result["error"] = "Tautulli viewer statistics unavailable"
        return result

    try:
        info = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_lookup, key): key for key in rating_keys}
            for future in as_completed(futures):
                result = future.result()
                info[result["rating_key"]] = result
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/tautulli-show-summaries", methods=["POST"])
def api_tautulli_show_summaries():
    """Batch-return cached TV episode counts and combined file sizes."""
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400

    raw_keys = body.get("rating_keys", [])
    if not isinstance(raw_keys, list):
        return jsonify({"error": "rating_keys must be a list"}), 400
    rating_keys = list(dict.fromkeys(str(key) for key in raw_keys if key is not None))[:50]

    def _lookup(rating_key):
        try:
            return rating_key, _get_show_episode_summary(rating_key)
        except Exception:
            return rating_key, {"episode_count": None, "file_size": 0, "error": True}

    try:
        info = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_lookup, key) for key in rating_keys]
            for future in futures:
                key, summary = future.result()
                info[key] = summary
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/tautulli-episodes")
def api_tautulli_episodes():
    """Return TV seasons, episodes, and per-user play stats for a show."""
    rating_key = request.args.get("rating_key")
    if not rating_key:
        return jsonify({"error": "rating_key required"}), 400

    def episode_viewers(episode):
        viewers = []
        play_count = 0
        try:
            for row in _cached_tautulli_viewers(episode["rating_key"]):
                if not isinstance(row, dict):
                    continue
                name = row.get("friendly_name") or row.get("username")
                try:
                    play_count += int(row.get("total_plays") or 0)
                except (TypeError, ValueError):
                    pass
                if name:
                    viewers.append({
                        "name": str(name),
                        "total_plays": row.get("total_plays", 0),
                    })
        except Exception:
            pass
        episode["play_count"] = play_count
        episode["viewers"] = viewers
        return episode

    try:
        show_data = tautulli.get_children_metadata(rating_key, "show")
        seasons = []
        for season in show_data.get("children_list", []) if isinstance(show_data, dict) else []:
            season_key = season.get("rating_key") if isinstance(season, dict) else None
            if not season_key:
                continue
            season_data = tautulli.get_children_metadata(season_key, "season")
            episodes = []
            children = season_data.get("children_list", []) if isinstance(season_data, dict) else []
            for episode in children:
                if not isinstance(episode, dict) or not episode.get("rating_key"):
                    continue
                episodes.append({
                    "rating_key": str(episode["rating_key"]),
                    "title": episode.get("title") or episode.get("full_title") or "Untitled episode",
                    "episode_number": episode.get("media_index") or episode.get("episode_num") or episode.get("index"),
                    "play_count": episode.get("play_count", 0),
                    "last_played": episode.get("last_played"),
                })
            with ThreadPoolExecutor(max_workers=8) as pool:
                episodes = list(pool.map(episode_viewers, episodes))
            seasons.append({
                "season_number": _season_number(season),
                "title": season.get("title") or season.get("full_title") or "Season",
                "episodes": episodes,
            })
        return jsonify({"rating_key": str(rating_key), "seasons": seasons})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _tv_request_status(history, episode, user_id, fallback_names, threshold):
    """Return a conservative playback state for one requester and episode."""
    if history is None or user_id is None and not fallback_names:
        return {"status": "unknown", "last_watched": None}
    matches = []
    for row in history:
        if not isinstance(row, dict):
            continue
        if row.get("rating_key") and str(row.get("rating_key")) != str(episode.get("rating_key")):
            continue
        history_id = row.get("user_id")
        if user_id is not None:
            if history_id is None or str(history_id) != str(user_id):
                continue
        else:
            names = {
                str(row.get(field) or "").strip().casefold()
                for field in ("friendly_name", "user", "username", "display_name")
            }
            if not names & fallback_names:
                continue
        matches.append(row)
    if not matches:
        return {"status": "not_watched", "last_watched": None}
    percentages = [_completion_percent(row) for row in matches]
    timestamps = [_history_timestamp(row) for row in matches]
    timestamps = [value for value in timestamps if value is not None]
    return {
        "status": "completed" if max(percentages) >= threshold else "partial",
        "last_watched": max(timestamps) if timestamps else None,
    }


def _episode_air_status(air_date):
    """Classify an episode with no Tautulli record without guessing."""
    if not air_date:
        return "unresolved"
    try:
        return "unaired" if date.fromisoformat(str(air_date)[:10]) > date.today() else "unavailable"
    except (TypeError, ValueError):
        return "unresolved"


def _series_status_label(status):
    """Normalize Seerr/TMDB series status to the labels used by the UI."""
    normalized = str(status or "").strip().casefold()
    if normalized in {"ended", "canceled", "cancelled", "finished"}:
        return "Ended"
    if normalized in {"continuing", "returning series", "in production", "ongoing"}:
        return "Continuing"
    return "Unknown"


def _tv_episode_key(season_number, episode_number):
    """Build a stable comparison key across numeric and string API values."""
    return (str(season_number), str(episode_number))


def _season_sort_key(season_number):
    """Sort numeric seasons before named seasons without comparing unlike types."""
    try:
        return (0, int(season_number))
    except (TypeError, ValueError):
        return (1, str(season_number or "").casefold())


def _season_number(season):
    """Return a season number even when Tautulli only supplies a title."""
    for field in ("parent_index", "index", "season_number", "seasonNumber"):
        value = season.get(field) if isinstance(season, dict) else None
        if value is not None and value != "":
            return value
    title = season.get("title") or season.get("full_title") if isinstance(season, dict) else ""
    match = re.search(r"(?:season|s)\s*([0-9]+)", str(title), re.IGNORECASE)
    return int(match.group(1)) if match else None


@api_bp.route("/tautulli-tv-request-details")
def api_tautulli_tv_request_details():
    """Return requested-season coverage and requester progress for one TV show."""
    rating_key = request.args.get("rating_key")
    if not rating_key:
        return jsonify({"error": "rating_key required"}), 400
    try:
        metadata = _unwrap_metadata(tautulli.get_metadata(rating_key))
        tmdb_id = extract_ids(metadata).get("tmdb")
        show_data = tautulli.get_children_metadata(rating_key, "show")
        seasons = []
        all_episodes = []
        for season in show_data.get("children_list", []) if isinstance(show_data, dict) else []:
            if not isinstance(season, dict) or not season.get("rating_key"):
                continue
            season_number = _season_number(season)
            season_data = tautulli.get_children_metadata(season["rating_key"], "season")
            episodes = []
            for episode in season_data.get("children_list", []) if isinstance(season_data, dict) else []:
                if not isinstance(episode, dict) or not episode.get("rating_key"):
                    continue
                item = {
                    "rating_key": str(episode["rating_key"]),
                    "season_number": season_number,
                    "episode_number": episode.get("media_index") or episode.get("episode_num") or episode.get("index"),
                    "title": episode.get("title") or episode.get("full_title") or "Untitled episode",
                    "air_date": episode.get("originally_available_at") or episode.get("air_date") or episode.get("airdate"),
                    "availability": "available",
                    "last_played": episode.get("last_played"),
                    "viewers": [],
                    "play_count": 0,
                }
                viewer_timestamps = []
                for row in _cached_tautulli_viewers(item["rating_key"]):
                    if isinstance(row, dict) and (row.get("friendly_name") or row.get("username")):
                        try:
                            item["play_count"] += int(row.get("total_plays") or 0)
                        except (TypeError, ValueError):
                            pass
                        item["viewers"].append({
                            "name": row.get("friendly_name") or row.get("username"),
                            "total_plays": row.get("total_plays", 0),
                            "last_played": row.get("last_played") or row.get("last_watched"),
                        })
                        timestamp = _history_timestamp(row)
                        if timestamp is not None:
                            viewer_timestamps.append(timestamp)
                if viewer_timestamps:
                    existing_timestamp = _history_timestamp({"last_played": item["last_played"]})
                    item["last_played"] = max(
                        viewer_timestamps + ([existing_timestamp] if existing_timestamp is not None else [])
                    )
                episodes.append(item)
                all_episodes.append(item)
            seasons.append({"season_number": season_number, "title": season.get("title") or "Season", "episodes": episodes})

        try:
            history = tautulli.get_item_history(rating_key, "show")
        except Exception:
            history = None
        if isinstance(history, list):
            for episode in all_episodes:
                timestamps = [
                    _history_timestamp(row)
                    for row in history
                    if isinstance(row, dict)
                    and str(row.get("rating_key")) == str(episode.get("rating_key"))
                ]
                timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
                if timestamps:
                    existing_timestamp = _history_timestamp({"last_played": episode["last_played"]})
                    episode["last_played"] = max(
                        timestamps + ([existing_timestamp] if existing_timestamp is not None else [])
                    )

        threshold = request.args.get("watched_threshold", type=float)
        if threshold is None or not 0 <= threshold <= 100:
            threshold = _DEFAULT_WATCHED_THRESHOLD
            try:
                settings = tautulli.get_settings()
                section = next((value for key, value in settings.items() if str(key).casefold() == "monitoring" and isinstance(value, dict)), {})
                configured = next((value for key, value in section.items() if str(key).casefold() == "movie_watched_percent"), None)
                configured = float(configured)
                if 0 <= configured <= 100:
                    threshold = configured
            except Exception:
                pass

        if not tmdb_id:
            return jsonify({"rating_key": str(rating_key), "seasons": seasons, "requesters": [],
                            "whole_series": {"episode_count": len(all_episodes)}, "error": "TMDB ID unavailable"})
        try:
            media = overseerr.overseerr_find_media(tmdb_id, "show") if OVERSEERR_API_KEY else None
        except Exception:
            media = None
        media_info = media.get("mediaInfo") if isinstance(media, dict) else None
        requests_for_show = media_info.get("requests") if isinstance(media_info, dict) else []
        requests_for_show = [item for item in requests_for_show if isinstance(item, dict)]

        requested_season_numbers = {
            season.get("seasonNumber")
            for request_item in requests_for_show
            for season in (request_item.get("seasons") or [])
            if isinstance(season, dict) and season.get("seasonNumber") is not None
        }
        catalog_season_numbers = set(requested_season_numbers)
        for season in (media.get("seasons") or []) if isinstance(media, dict) else []:
            if isinstance(season, dict):
                season_number = season.get("seasonNumber") or season.get("season_number")
                if season_number is not None:
                    catalog_season_numbers.add(season_number)
        season_catalog = {}
        if catalog_season_numbers:
            def load_season(season_number):
                try:
                    return season_number, overseerr.overseerr_find_tv_season(tmdb_id, season_number)
                except Exception:
                    return season_number, None

            with ThreadPoolExecutor(max_workers=4) as pool:
                for season_number, season_data in pool.map(load_season, sorted(catalog_season_numbers, key=_season_sort_key)):
                    season_catalog[season_number] = season_data

        tautulli_by_episode = {
            _tv_episode_key(episode.get("season_number"), episode.get("episode_number")): episode
            for episode in all_episodes
        }
        catalog_by_season = {}
        for season_number, season_data in season_catalog.items():
            if not isinstance(season_data, dict):
                continue
            episodes = season_data.get("episodes") or season_data.get("results") or []
            catalog_by_season[str(season_number)] = [
                {
                    "season_number": season_number,
                    "episode_number": item.get("episodeNumber") or item.get("episode_number"),
                    "title": item.get("name") or item.get("title") or "Untitled episode",
                    "air_date": item.get("airDate") or item.get("air_date"),
                }
                for item in episodes
                if isinstance(item, dict) and (item.get("episodeNumber") or item.get("episode_number")) is not None
            ]

        season_by_number = {
            str(season.get("season_number")): season
            for season in seasons
            if season.get("season_number") is not None
        }
        for season_key, catalog_episodes in catalog_by_season.items():
            season_number = catalog_episodes[0].get("season_number") if catalog_episodes else season_key
            season = season_by_number.get(season_key)
            if season is None:
                season = {"season_number": season_number, "title": f"Season {season_number}", "episodes": []}
                seasons.append(season)
                season_by_number[season_key] = season
            for expected in catalog_episodes:
                existing = next(
                    (
                        episode
                        for episode in season["episodes"]
                        if str(episode.get("episode_number")) == str(expected.get("episode_number"))
                    ),
                    None,
                )
                if existing is not None:
                    if not existing.get("air_date") and expected.get("air_date"):
                        existing["air_date"] = expected["air_date"]
                    continue
                season["episodes"].append({
                    "rating_key": None,
                    "title": expected.get("title") or "Untitled episode",
                    "episode_number": expected.get("episode_number"),
                    "air_date": expected.get("air_date"),
                    "availability": _episode_air_status(expected.get("air_date")),
                    "last_played": None,
                    "viewers": [],
                    "play_count": 0,
                })
            season["episodes"].sort(key=lambda episode: _season_sort_key(episode.get("episode_number")))
        seasons.sort(key=lambda season: _season_sort_key(season.get("season_number")))
        future_air_dates = []
        past_air_dates = []
        for season in seasons:
            for episode in season.get("episodes", []):
                air_date = episode.get("air_date")
                if not air_date:
                    continue
                try:
                    parsed_air_date = date.fromisoformat(str(air_date)[:10])
                except (TypeError, ValueError):
                    continue
                if parsed_air_date > date.today():
                    future_air_dates.append(parsed_air_date.isoformat())
                else:
                    past_air_dates.append(parsed_air_date.isoformat())

        tautulli_users = _tautulli_users_cache[1] if _tautulli_users_cache else []
        if not tautulli_users:
            try:
                tautulli_users = [user for user in tautulli.get_users() if isinstance(user, dict)]
            except Exception:
                tautulli_users = []
        users_by_id = {str(user.get("user_id")): user for user in tautulli_users if user.get("user_id") is not None}
        result_requesters = []
        for request_item in requests_for_show:
            user = request_item.get("requestedBy") or {}
            if user.get("plexId") is None and user.get("id") is not None:
                user = overseerr.overseerr_find_user(user["id"]) or user
            name = user.get("displayName") or user.get("plexUsername") or user.get("email") or "Unknown requester"
            plex_id = user.get("plexId")
            tautulli_user = users_by_id.get(str(plex_id)) if plex_id is not None else None
            fallback_names = _identity_values(user)
            if tautulli_user:
                user_id, identity = tautulli_user.get("user_id"), "stable_id"
            elif plex_id is None:
                match = next((candidate for candidate in tautulli_users if _identity_values(candidate) & fallback_names), None)
                user_id, identity = (match.get("user_id"), "name_fallback") if match else (None, "unknown")
            else:
                user_id, identity = None, "unknown"
            requested_seasons = {
                item.get("seasonNumber") for item in (request_item.get("seasons") or [])
                if isinstance(item, dict) and item.get("seasonNumber") is not None
            }
            requested_catalog = []
            missing_seasons = []
            for season_number in sorted(requested_seasons, key=_season_sort_key):
                season_episodes = catalog_by_season.get(str(season_number))
                if season_episodes is None:
                    fallback = [episode for episode in all_episodes if episode.get("season_number") == season_number]
                    if fallback:
                        season_episodes = fallback
                    else:
                        missing_seasons.append(season_number)
                        continue
                requested_catalog.extend(season_episodes)
            statuses = []
            for expected in requested_catalog:
                episode = tautulli_by_episode.get(_tv_episode_key(expected.get("season_number"), expected.get("episode_number")))
                if episode:
                    status = _tv_request_status(history, episode, user_id, fallback_names if identity == "name_fallback" else set(), threshold)
                    availability = "available"
                    rating_key_value = episode["rating_key"]
                else:
                    availability = _episode_air_status(expected.get("air_date"))
                    status = {"status": "unknown", "last_watched": None}
                    rating_key_value = None
                statuses.append({
                    "rating_key": rating_key_value,
                    "season_number": expected.get("season_number"),
                    "episode_number": expected.get("episode_number"),
                    "title": expected.get("title") or (episode or {}).get("title") or "Untitled episode",
                    "air_date": expected.get("air_date") or (episode or {}).get("air_date"),
                    "availability": availability,
                    **status,
                })
            counts = {key: sum(1 for item in statuses if item["status"] == key and item["availability"] == "available") for key in ("completed", "partial", "not_watched", "unknown")}
            whole_series_statuses = [
                _tv_request_status(history, episode, user_id, fallback_names if identity == "name_fallback" else set(), threshold)
                for episode in all_episodes
            ]
            whole_series_counts = {
                key: sum(1 for item in whole_series_statuses if item["status"] == key)
                for key in ("completed", "partial", "not_watched", "unknown")
            }
            result_requesters.append({
                "name": name, "identity_match": identity,
                "requested_seasons": sorted(requested_seasons, key=_season_sort_key), "missing_seasons": sorted(missing_seasons, key=_season_sort_key),
                "episodes": statuses, "available_count": sum(1 for item in statuses if item["availability"] == "available"),
                "unavailable_count": sum(1 for item in statuses if item["availability"] == "unavailable"),
                "unaired_count": sum(1 for item in statuses if item["availability"] == "unaired"),
                "unresolved_count": sum(1 for item in statuses if item["availability"] == "unresolved"),
                "progress": counts, "whole_series_progress": whole_series_counts,
            })
        series_status = _series_status_label(
            (media or {}).get("status") if isinstance(media, dict) else metadata.get("status")
        )
        if future_air_dates:
            air_date_label = "Next air date"
            air_date_value = min(future_air_dates)
        elif series_status == "Ended" and past_air_dates:
            air_date_label = "Last air date"
            air_date_value = max(past_air_dates)
        else:
            air_date_label = "Next air date"
            air_date_value = None
        return jsonify({"rating_key": str(rating_key), "seasons": seasons, "requesters": result_requesters,
                        "series_status": series_status, "watched_threshold": threshold,
                        "air_date_label": air_date_label, "air_date_value": air_date_value,
                        "whole_series": {"episode_count": len(all_episodes)}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/item-ids")
def api_item_ids():
    """Return guid and extracted IDs for a rating_key (for remove flow when library item has no guid)."""
    rating_key = request.args.get("rating_key")
    if not rating_key:
        return jsonify({"error": "rating_key required"}), 400
    try:
        meta = tautulli.get_metadata(rating_key)
        if isinstance(meta, list) and meta:
            meta = meta[0]
        if isinstance(meta, dict) and "metadata" in meta and isinstance(meta["metadata"], dict):
            meta = meta["metadata"]
        if not isinstance(meta, dict):
            meta = {}
        ids = extract_ids(meta)
        return jsonify({
            "guid": meta.get("guid") or meta.get("grandparent_guid"),
            "tmdb_id": ids["tmdb"],
            "tvdb_id": ids["tvdb"],
            "imdb_id": ids["imdb"],
            "mbid": ids["mbid"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/item-poster")
def api_item_poster():
    """Proxy a Plex poster for a library item without exposing the Plex token."""
    rating_key = request.args.get("rating_key")
    if not rating_key:
        return jsonify({"error": "rating_key required"}), 400
    if not PLEX_URL or not PLEX_TOKEN:
        return jsonify({"error": "Plex not configured"}), 404
    try:
        meta = tautulli.get_metadata(rating_key)
        if isinstance(meta, list) and meta:
            meta = meta[0]
        if isinstance(meta, dict) and isinstance(meta.get("metadata"), dict):
            meta = meta["metadata"]
        thumb = meta.get("thumb") if isinstance(meta, dict) else None
        if not thumb:
            return jsonify({"error": "Poster unavailable"}), 404
        poster_url = thumb if str(thumb).startswith("http") else f"{PLEX_URL.rstrip('/')}/{str(thumb).lstrip('/')}"
        response = requests.get(
            poster_url,
            params={"X-Plex-Token": PLEX_TOKEN},
            timeout=15,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "image/jpeg")
        return Response(
            response.content,
            status=200,
            mimetype=content_type.split(";", 1)[0],
            headers={"Cache-Control": "private, max-age=300"},
        )
    except Exception:
        return jsonify({"error": "Poster unavailable"}), 404


@api_bp.route("/refresh-plex", methods=["POST"])
def api_refresh_plex():
    """Batch refresh Plex libraries for given section_ids (called after all removals complete).

    Expects JSON:
    {
        "section_ids": ["1", "2", ...]  or [{"section_id": "1", "section_type": "movie"}, ...]
    }
    """
    if not PLEX_URL or not PLEX_TOKEN:
        return jsonify({"error": "Plex not configured"}), 400
    body = request.get_json(force=True, silent=True) or {}
    section_ids = body.get("section_ids", [])
    if not section_ids:
        return jsonify({"refreshed": []})
    refreshed = []
    errors = []
    for item in section_ids:
        # Handle both string (legacy) and dict formats
        if isinstance(item, dict):
            sid = str(item.get("section_id", ""))
        else:
            sid = str(item)
        if not sid:
            continue
        try:
            plex_svc.plex_refresh_library(sid)
            refreshed.append(sid)
        except Exception as e:
            errors.append({"section_id": sid, "error": str(e)})
    return jsonify({"refreshed": refreshed, "errors": errors})


@api_bp.route("/refresh-tautulli", methods=["POST"])
def api_refresh_tautulli():
    """Batch refresh Tautulli media info for given sections (called after Plex refresh).

    Expects JSON:
    {
        "sections": [{"section_id": "1", "section_type": "movie"}, ...]
    }
    """
    body = request.get_json(force=True, silent=True) or {}
    sections = body.get("sections", [])
    if not sections:
        return jsonify({"refreshed": []})
    refreshed = []
    errors = []
    for section in sections:
        sid = section.get("section_id")
        section_type = section.get("section_type")
        if not sid:
            continue
        try:
            if tautulli.refresh_tautulli_media_info(str(sid), section_type):
                refreshed.append(sid)
            else:
                errors.append({"section_id": sid, "error": "Refresh failed"})
        except Exception as e:
            errors.append({"section_id": sid, "error": str(e)})
    return jsonify({"refreshed": refreshed, "errors": errors})


@api_bp.route("/remove-options")
def api_remove_options():
    """Return non-sensitive choices needed before a removal is confirmed."""
    return jsonify({
        "radarr_instances": [
            {"index": index, "name": instance.get("name") or f"Radarr {index + 1}"}
            for index, instance in enumerate(RADARR_INSTANCES)
        ],
    })


@api_bp.route("/remove", methods=["POST"])
def api_remove():
    """
    Remove media from Seerr and Radarr/Sonarr/Lidarr (all instances).
    Tautulli is not modified — items will disappear after Plex scans and Tautulli refreshes media info.

    Expects JSON:
    {
        "rating_key": "...",
        "section_id": "...",
        "media_type": "movie" | "show" | "artist",
        "guid": "...",       (optional — from library item or /api/item-ids)
        "tmdb_id": "...",   (optional — resolved via guid or Tautulli if missing)
        "tvdb_id": "...",
        "imdb_id": "...",
        "mbid": "...",
        "confirmed": true,
        "radarr_instance": 0  # required for movie removal; zero-based index
    }
    """
    body = request.get_json(force=True)
    rating_key = body.get("rating_key")
    section_id = body.get("section_id")
    media_type = body.get("media_type", "movie")
    guid = body.get("guid")
    title = body.get("title")
    year = body.get("year")
    tmdb_id = body.get("tmdb_id")
    tvdb_id = body.get("tvdb_id")
    imdb_id = body.get("imdb_id")
    mbid = body.get("mbid")
    radarr_instance_index = body.get("radarr_instance")

    # The browser confirmation is part of the destructive-operation contract;
    # callers must also identify exactly one Radarr copy for a movie.
    if body.get("confirmed") is not True:
        return jsonify({"error": "Explicit removal confirmation is required"}), 400
    if media_type == "movie":
        try:
            radarr_instance_index = int(radarr_instance_index)
        except (TypeError, ValueError):
            return jsonify({"error": "A Radarr instance must be selected for movie removal"}), 400
        if not 0 <= radarr_instance_index < len(RADARR_INSTANCES):
            return jsonify({"error": "Selected Radarr instance is not configured"}), 400

    # Resolve IDs from guid first (from library item); then from Tautulli get_metadata
    if guid:
        ids_from_guid = extract_ids({"guid": guid})
        tmdb_id = tmdb_id or ids_from_guid["tmdb"]
        tvdb_id = tvdb_id or ids_from_guid["tvdb"]
        imdb_id = imdb_id or ids_from_guid["imdb"]
        mbid = mbid or ids_from_guid["mbid"]

    results = {"overseerr": None, "tautulli": None}

    try:
        if rating_key:
            needs_resolve = (
                (not tmdb_id and not imdb_id)
                or (media_type == "show" and not tvdb_id)
                or (media_type == "artist" and not mbid)
            )
            if needs_resolve:
                meta_raw = tautulli.get_metadata(rating_key)
                # Pass raw response so deep scan finds guids anywhere in the structure
                ids = extract_ids(meta_raw)
                tmdb_id = tmdb_id or ids["tmdb"]
                tvdb_id = tvdb_id or ids["tvdb"]
                imdb_id = imdb_id or ids["imdb"]
                mbid = mbid or ids["mbid"]

                # Fallback: find item in library media by rating_key and use its guid
                if (not tmdb_id and not imdb_id and media_type == "movie") or (
                    media_type == "show" and not tvdb_id
                ):
                    try:
                        if section_id:
                            lib_data = tautulli.get_library_media(
                                section_id,
                                length=500,
                                start=0,
                                section_type=media_type,
                            )
                            items = lib_data.get("data") if isinstance(lib_data.get("data"), list) else []
                            for item in items:
                                if isinstance(item, dict) and str(item.get("rating_key")) == str(rating_key):
                                    ids2 = extract_ids(item)
                                    tmdb_id = tmdb_id or ids2["tmdb"]
                                    tvdb_id = tvdb_id or ids2["tvdb"]
                                    imdb_id = imdb_id or ids2["imdb"]
                                    mbid = mbid or ids2["mbid"]
                                    break
                    except Exception:
                        pass

        has_ids = tmdb_id or tvdb_id or imdb_id or mbid

        if not has_ids:
            results["overseerr"] = "skipped (no IDs resolved)"
        elif media_type == "artist":
            results["overseerr"] = "skipped (music)"
        else:
            try:
                if tmdb_id:
                    media = overseerr.overseerr_find_media(tmdb_id, media_type)
                    if media and media.get("mediaInfo"):
                        media_id = media["mediaInfo"]["id"]
                        overseerr.overseerr_delete_media(media_id)
                        results["overseerr"] = "removed"
                    else:
                        results["overseerr"] = "not_found"
                else:
                    results["overseerr"] = "skipped (no TMDB id)"
            except Exception as e:
                results["overseerr"] = f"error: {e}"

        if not has_ids:
            skip_msg = "skipped (no IDs resolved)"
            results["arr"] = skip_msg
            if media_type == "movie":
                # Explicit, conservative fallback for legacy items without IDs.
                if title and str(title).strip():
                    inst = RADARR_INSTANCES[radarr_instance_index]
                    key = f"radarr_{radarr_instance_index + 1}"
                    try:
                        candidates = radarr.radarr_find_movie_candidates_by_title(inst, title, year)
                        if len(candidates) == 1:
                            radarr.radarr_delete_movie(inst, candidates[0]["id"], delete_files=False)
                            results[key] = "removed"
                            results[f"{key}_match"] = "title_fallback"
                        elif len(candidates) > 1:
                            results[key] = "ambiguous (title fallback)"
                        else:
                            results[key] = "unresolved (title fallback)"
                    except Exception as e:
                        results[key] = f"error: {e}"
                else:
                    results[f"radarr_{radarr_instance_index + 1}"] = skip_msg
            elif media_type == "show":
                for i in range(len(SONARR_INSTANCES)):
                    results[f"sonarr_{i + 1}"] = skip_msg
            elif media_type == "artist":
                for i in range(len(LIDARR_INSTANCES)):
                    results[f"lidarr_{i + 1}"] = skip_msg
        elif media_type == "movie":
            inst = RADARR_INSTANCES[radarr_instance_index]
            key = f"radarr_{radarr_instance_index + 1}"
            try:
                movie = radarr.radarr_find_movie(
                    inst, tmdb_id=tmdb_id, imdb_id=imdb_id
                )
                if movie:
                    # Keep completed/download files for the existing torrent
                    # cleanup workflow; remove only the Radarr library entry.
                    radarr.radarr_delete_movie(inst, movie["id"], delete_files=False)
                    results[key] = "removed"
                else:
                    results[key] = "unresolved (stable ID not found)"
            except Exception as e:
                results[key] = f"error: {e}"
        elif media_type == "artist":
            for i, inst in enumerate(LIDARR_INSTANCES):
                key = f"lidarr_{i + 1}"
                try:
                    if mbid:
                        artist = lidarr.lidarr_find_artist(inst, mbid)
                        if artist:
                            lidarr.lidarr_delete_artist(
                                inst, artist["id"], delete_files=True
                            )
                            results[key] = "removed"
                        else:
                            results[key] = "not_found"
                    else:
                        results[key] = "skipped (no MusicBrainz id)"
                except Exception as e:
                    results[key] = f"error: {e}"
        else:
            for i, inst in enumerate(SONARR_INSTANCES):
                key = f"sonarr_{i + 1}"
                try:
                    series = None
                    if tvdb_id:
                        series = sonarr.sonarr_find_series(inst, tvdb_id)
                    if not series and tmdb_id:
                        series = sonarr.sonarr_find_series_by_tmdb(inst, tmdb_id)
                    if series:
                        sonarr.sonarr_delete_series(
                            inst, series["id"], delete_files=True
                        )
                        results[key] = "removed"
                    else:
                        results[key] = "not_found"
                except Exception as e:
                    results[key] = f"error: {e}"

        # Plex refresh handled by batch endpoint after all items are processed
        arr_succeeded = any(
            v == "removed"
            for k, v in results.items()
            if k.startswith("radarr_") or k.startswith("sonarr_") or k.startswith("lidarr_")
        )
        if arr_succeeded and section_id:
            results["_section_id_for_refresh"] = section_id
        results["plex"] = "pending" if arr_succeeded and section_id else "skipped"

        # Tautulli removal disabled — items will disappear after Plex scans and Tautulli refreshes media info
        results["tautulli"] = "skipped"

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
