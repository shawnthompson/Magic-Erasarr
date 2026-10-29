"""Seerr API client (Overseerr-compatible)."""
import requests

from config import OVERSEERR_API_KEY, OVERSEERR_URL


def overseerr_headers() -> dict:
    return {"X-Api-Key": OVERSEERR_API_KEY, "Content-Type": "application/json"}


def overseerr_find_media(tmdb_id, media_type: str = "movie") -> dict | None:
    """Look up media in Seerr by TMDB id.

    media_type: "movie" or "show"
    """
    if not OVERSEERR_API_KEY:
        raise ValueError("OVERSEERR_API_KEY is not set — check your .env file")
    endpoint = "movie" if media_type == "movie" else "tv"
    r = requests.get(
        f"{OVERSEERR_URL}/api/v1/{endpoint}/{tmdb_id}",
        headers=overseerr_headers(),
        timeout=15,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def overseerr_find_tv_season(tmdb_id, season_number) -> dict | None:
    """Look up one TV season and its episode catalog in Seerr."""
    if not OVERSEERR_API_KEY:
        raise ValueError("OVERSEERR_API_KEY is not set — check your .env file")
    r = requests.get(
        f"{OVERSEERR_URL}/api/v1/tv/{tmdb_id}/season/{season_number}",
        headers=overseerr_headers(),
        timeout=15,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def overseerr_find_user(user_id) -> dict | None:
    """Look up a Seerr user by its internal user ID."""
    if not OVERSEERR_API_KEY:
        raise ValueError("OVERSEERR_API_KEY is not set — check your .env file")
    r = requests.get(
        f"{OVERSEERR_URL}/api/v1/user/{user_id}",
        headers=overseerr_headers(),
        timeout=15,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def overseerr_delete_media(media_id) -> bool:
    """Delete a media entry from Seerr (removes request + clears data)."""
    r = requests.delete(
        f"{OVERSEERR_URL}/api/v1/media/{media_id}",
        headers=overseerr_headers(),
        timeout=15,
    )
    r.raise_for_status()
    return True
