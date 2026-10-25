"""API routes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
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

_VIEWER_CACHE_TTL = 300
_viewer_cache = {}
_viewer_cache_lock = Lock()

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

        # File size normalization for shows
        if section_type == "show":
            for item in page_items:
                if isinstance(item, dict):
                    fs, tfs = item.get("file_size"), item.get("total_file_size")
                    if (fs is None or fs == 0 or fs == "") and tfs is not None:
                        item["file_size"] = tfs
                    if (item.get("file_size") or 0) == 0:
                        for key in ("total_file_size", "size", "total_size"):
                            v = item.get(key)
                            if v is not None and v != "":
                                try:
                                    n = int(v) if isinstance(v, str) else v
                                    if n > 0:
                                        item["file_size"] = n
                                        break
                                except (TypeError, ValueError):
                                    pass

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
        "media_type": "movie" | "show"
    }

    Returns a dict keyed by rating_key with requestor info.
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400
    rating_keys = body.get("rating_keys", [])
    media_type = body.get("media_type", "movie")

    if not OVERSEERR_API_KEY:
        return jsonify({})

    def _lookup(rk):
        result = {"rating_key": rk, "requested_by": None}
        try:
            meta = tautulli.get_metadata(rk)
            ids = extract_ids(meta)
            tmdb_id = ids.get("tmdb")
            if not tmdb_id:
                return result

            media = overseerr.overseerr_find_media(tmdb_id, media_type)
            if not media:
                return result

            media_info = media.get("mediaInfo")
            if not media_info:
                return result

            reqs = media_info.get("requests") or []
            requestors = []
            for req in reqs:
                user = req.get("requestedBy") or {}
                name = (
                    user.get("displayName")
                    or user.get("plexUsername")
                    or user.get("email")
                    or None
                )
                if name and name not in requestors:
                    requestors.append(name)
            if requestors:
                result["requested_by"] = ", ".join(requestors)
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


@api_bp.route("/tautulli-episodes")
def api_tautulli_episodes():
    """Return TV seasons, episodes, and per-user play stats for a show."""
    rating_key = request.args.get("rating_key")
    if not rating_key:
        return jsonify({"error": "rating_key required"}), 400

    def episode_viewers(episode):
        viewers = []
        try:
            for row in _cached_tautulli_viewers(episode["rating_key"]):
                if not isinstance(row, dict):
                    continue
                name = row.get("friendly_name") or row.get("username")
                if name:
                    viewers.append({
                        "name": str(name),
                        "total_plays": row.get("total_plays", 0),
                    })
        except Exception:
            pass
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
            seasons.append({
                "season_number": season.get("parent_index") or season.get("index"),
                "title": season.get("title") or season.get("full_title") or "Season",
                "episodes": [episode_viewers(episode) for episode in episodes],
            })
        return jsonify({"rating_key": str(rating_key), "seasons": seasons})
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
        "mbid": "..."
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
                # Fallback: find movie in Radarr by title (+ year) and delete
                if title and str(title).strip():
                    for i, inst in enumerate(RADARR_INSTANCES):
                        key = f"radarr_{i + 1}"
                        try:
                            movie = radarr.radarr_find_movie_by_title(inst, title, year)
                            if movie:
                                radarr.radarr_delete_movie(inst, movie["id"], delete_files=True)
                                results[key] = "removed"
                            else:
                                results[key] = "not_found"
                        except Exception as e:
                            results[key] = f"error: {e}"
                else:
                    for i in range(len(RADARR_INSTANCES)):
                        results[f"radarr_{i + 1}"] = skip_msg
            elif media_type == "show":
                for i in range(len(SONARR_INSTANCES)):
                    results[f"sonarr_{i + 1}"] = skip_msg
            elif media_type == "artist":
                for i in range(len(LIDARR_INSTANCES)):
                    results[f"lidarr_{i + 1}"] = skip_msg
        elif media_type == "movie":
            for i, inst in enumerate(RADARR_INSTANCES):
                key = f"radarr_{i + 1}"
                try:
                    movie = radarr.radarr_find_movie(
                        inst, tmdb_id=tmdb_id, imdb_id=imdb_id
                    )
                    if movie:
                        radarr.radarr_delete_movie(inst, movie["id"], delete_files=True)
                        results[key] = "removed"
                    else:
                        results[key] = "not_found"
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
