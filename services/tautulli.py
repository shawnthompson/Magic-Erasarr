"""Tautulli API client."""
import requests

from config import TAUTULLI_API_KEY, TAUTULLI_URL

# Keep under typical gunicorn worker timeout so we get TimeoutError, not worker kill
TAUTULLI_TIMEOUT = 15


def tautulli_get(cmd: str, params: dict | None = None, timeout: int | None = None) -> dict | list:
    """Call the Tautulli API."""
    if not TAUTULLI_API_KEY:
        raise ValueError("TAUTULLI_API_KEY is not set — check your .env file")
    p = {"apikey": TAUTULLI_API_KEY, "cmd": cmd}
    if params:
        p.update(params)
    url = f"{TAUTULLI_URL}/api/v2"
    r = requests.get(url, params=p, timeout=timeout or TAUTULLI_TIMEOUT)
    r.raise_for_status()
    ct = r.headers.get("Content-Type", "")
    if "json" not in ct and "javascript" not in ct:
        raise ValueError(
            f"Tautulli returned non-JSON (Content-Type: {ct}). "
            f"Check TAUTULLI_URL ({TAUTULLI_URL}) and TAUTULLI_API_KEY."
        )
    data = r.json()
    resp = data.get("response", {})
    if resp.get("result") != "success":
        msg = resp.get("message", "unknown error")
        raise ValueError(f"Tautulli API error: {msg}")
    return resp.get("data", {})


def tautulli_get_response(cmd: str, params: dict | None = None, timeout: int | None = None) -> dict:
    """Call the Tautulli API and return the full response dict (result, message, data) without raising.
    Use this when you need to inspect the raw response (e.g. to detect 'calculating file sizes').
    """
    if not TAUTULLI_API_KEY:
        raise ValueError("TAUTULLI_API_KEY is not set — check your .env file")
    p = {"apikey": TAUTULLI_API_KEY, "cmd": cmd}
    if params:
        p.update(params)
    url = f"{TAUTULLI_URL}/api/v2"
    r = requests.get(url, params=p, timeout=timeout or TAUTULLI_TIMEOUT)
    r.raise_for_status()
    ct = r.headers.get("Content-Type", "")
    if "json" not in ct and "javascript" not in ct:
        raise ValueError(
            f"Tautulli returned non-JSON (Content-Type: {ct}). "
            f"Check TAUTULLI_URL ({TAUTULLI_URL}) and TAUTULLI_API_KEY."
        )
    data = r.json()
    return data.get("response", {})


def get_tautulli_libraries() -> list:
    """Return the list of Tautulli libraries."""
    data = tautulli_get("get_libraries")
    return data if isinstance(data, list) else []


def get_library_media(
    section_id,
    length: int = 50,
    start: int = 0,
    search: str | None = None,
    order_column: str = "last_played",
    order_dir: str = "asc",
    section_type: str | None = None,
):
    """Fetch media from a Tautulli library section."""
    params = {
        "section_id": section_id,
        "length": length,
        "start": start,
        "order_column": order_column,
        "order_dir": order_dir,
    }
    if search:
        params["search"] = search
    if section_type:
        params["section_type"] = section_type
    return tautulli_get("get_library_media_info", params)


def response_indicates_calculating_file_sizes(resp: dict) -> bool:
    """Return True if the Tautulli response indicates 'calculating file sizes'.

    Tautulli does not return an error or message when calculating; it returns success with
    total_file_size/filtered_file_size as 0 while records exist. The UI shows
    'Tautulli is calculating the file sizes for the library's media info' in that state.
    We also check for an explicit message in response or data.message (e.g. from other versions).
    """
    if not isinstance(resp, dict):
        return False
    msg = (resp.get("message") or "").lower()
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    data_msg = (data.get("message") or data.get("msg") or "").lower()
    for text in (msg, data_msg):
        if "calculating" in text and ("file size" in text or "file sizes" in text or "filesize" in text):
            return True
    # Heuristic: success response with rows but no file size totals yet
    if resp.get("result") == "success" and isinstance(data, dict):
        total = data.get("recordsTotal") or data.get("recordsFiltered") or 0
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = 0
        total_fs = data.get("total_file_size")
        filtered_fs = data.get("filtered_file_size")
        try:
            total_fs = int(total_fs) if total_fs not in (None, "") else 0
        except (TypeError, ValueError):
            total_fs = 0
        try:
            filtered_fs = int(filtered_fs) if filtered_fs not in (None, "") else 0
        except (TypeError, ValueError):
            filtered_fs = 0
        if total > 0 and total_fs == 0 and filtered_fs == 0:
            return True
    return False


def get_library_media_response(
    section_id,
    length: int = 50,
    start: int = 0,
    search: str | None = None,
    order_column: str = "last_played",
    order_dir: str = "asc",
    section_type: str | None = None,
) -> dict:
    """Fetch library media and return the full Tautulli response (result, message, data).
    Use this to detect states like 'calculating file sizes' from response.message or response.data.
    """
    params = {
        "section_id": section_id,
        "length": length,
        "start": start,
        "order_column": order_column,
        "order_dir": order_dir,
    }
    if search:
        params["search"] = search
    if section_type:
        params["section_type"] = section_type
    return tautulli_get_response("get_library_media_info", params)


def get_metadata(rating_key) -> dict:
    """Get Tautulli metadata for a single item (includes guids)."""
    return tautulli_get("get_metadata", {"rating_key": rating_key})


def get_children_metadata(rating_key, media_type: str) -> dict:
    """Get metadata for the direct children of a show or season."""
    return tautulli_get(
        "get_children_metadata",
        {"rating_key": rating_key, "media_type": media_type},
    )


def get_item_user_stats(rating_key) -> list:
    """Get aggregate play statistics for each Tautulli user of an item."""
    data = tautulli_get("get_item_user_stats", {"rating_key": rating_key})
    return data if isinstance(data, list) else []


def delete_tautulli_history(rating_key) -> int:
    """Delete all Tautulli play history for a rating key."""
    data = tautulli_get("get_history", {
        "rating_key": rating_key,
        "length": 10000,
    })
    # Response can be dict with "data" list, or (in some versions) the list itself
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") if isinstance(data.get("data"), list) else []
    else:
        rows = []
    deleted = 0
    if rows:
        # Tautulli uses "row_id" in get_history response; fallback to "id"
        row_ids = ",".join(
            str(r.get("row_id") or r.get("id"))
            for r in rows
            if isinstance(r, dict) and (r.get("row_id") is not None or r.get("id") is not None)
        )
        if row_ids:
            try:
                tautulli_get("delete_history", {"row_ids": row_ids})
                deleted = len(rows)
            except Exception:
                pass
    return deleted


def refresh_tautulli_media_info(section_id: str, section_type: str | None = None) -> bool:
    """Refresh Tautulli media info for a library section.

    Triggers Tautulli to refresh its media info cache from Plex by calling
    get_library_media_info with refresh="true". After Plex scans detect deleted
    files, this ensures Tautulli's cache reflects those changes.
    """
    try:
        params = {"section_id": section_id, "length": 1, "start": 0, "refresh": "true"}
        if section_type:
            params["section_type"] = section_type
        tautulli_get("get_library_media_info", params)
        return True
    except Exception:
        return False


def delete_tautulli_media_info_cache(section_id: str, section_type: str | None = None) -> None:
    """Clear the media info table cache for a library section and trigger refresh.

    Tautulli API only accepts section_id (clears the whole section's cache).
    We then request a refresh so the cache repopulates from Plex; items no longer
    in Plex (e.g. removed from Radarr) will not appear in the new cache.
    """
    tautulli_get("delete_media_info_cache", {"section_id": section_id})
    # Force Tautulli to rebuild the media info table from Plex
    params = {"section_id": section_id, "length": 1, "start": 0, "refresh": "true"}
    if section_type:
        params["section_type"] = section_type
    try:
        tautulli_get("get_library_media_info", params)
    except Exception:
        pass
