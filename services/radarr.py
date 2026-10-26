"""Radarr API client (multi-instance)."""
import re
import requests


def _normalize_imdb(val):
    """Normalize IMDB id for comparison (e.g. tt123 vs tt0123)."""
    if not val:
        return ""
    s = str(val).strip().lower()
    if s.startswith("tt"):
        digits = re.sub(r"^tt0*", "", s)
        return f"tt{digits}" if digits else s
    return s


def _movie_list(response):
    """Return list of movies from Radarr GET /movie response (list or dict)."""
    data = response.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("records") or data.get("movie") or data.get("movies") or []
    return []


def _normalize_title(s: str) -> str:
    """Normalize title for matching: lower, strip punctuation, collapse spaces."""
    if not s:
        return ""
    s = str(s).strip().lower()
    # Remove punctuation (keep alphanumeric and spaces)
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def _titles_match(want: str, got: str) -> bool:
    """True if want and got match exactly or one contains the other (after normalize)."""
    if not want or not got:
        return False
    if want == got:
        return True
    # Substring match so "Afro Samurai Resurrection" matches "Afro Samurai Resurrection 2009" or vice versa
    return want in got or got in want


def radarr_find_movie_by_title(instance: dict, title: str, year=None) -> dict | None:
    """Find a movie in a Radarr instance by title (and optional year)."""
    candidates = radarr_find_movie_candidates_by_title(instance, title, year)
    if len(candidates) != 1:
        return None
    return candidates[0]


def radarr_find_movie_candidates_by_title(instance: dict, title: str, year=None) -> list[dict]:
    """Return all title/year candidates without guessing between them."""
    if not title or not str(title).strip():
        return []
    r = requests.get(
        f"{instance['url']}/api/v3/movie",
        params={"apikey": instance["api_key"]},
        timeout=30,
    )
    r.raise_for_status()
    want_title = _normalize_title(title)
    want_year = None
    if year is not None and str(year).strip():
        try:
            want_year = int(year)
        except (TypeError, ValueError):
            pass
    candidates = []
    for m in _movie_list(r):
        if not isinstance(m, dict):
            continue
        t = _normalize_title(m.get("title") or m.get("originalTitle") or "")
        if not _titles_match(want_title, t):
            continue
        y = m.get("year")
        if y is not None:
            try:
                y = int(y)
            except (TypeError, ValueError):
                y = None
        if want_year is not None and y is not None and y != want_year:
            continue
        candidates.append((m, y))
    if not candidates:
        return []
    # Exact title/year matches are the only safe title fallback. If there are
    # still multiple matches, the caller must report the ambiguity.
    exact = [
        m for m, y in candidates
        if _normalize_title(m.get("title") or m.get("originalTitle") or "") == want_title
        and (want_year is None or y == want_year)
    ]
    return exact or [m for m, _ in candidates]


def radarr_find_movie(instance: dict, tmdb_id=None, imdb_id=None) -> dict | None:
    """Find a movie in a Radarr instance by TMDB or IMDB id."""
    base = f"{instance['url']}/api/v3/movie"
    params = {"apikey": instance["api_key"]}
    if tmdb_id:
        params["tmdbId"] = tmdb_id
        r = requests.get(base, params=params, timeout=15)
        r.raise_for_status()
        movies = _movie_list(r)
        if movies:
            return movies[0]
    if imdb_id:
        r = requests.get(base, params={"apikey": instance["api_key"]}, timeout=30)
        r.raise_for_status()
        want = _normalize_imdb(imdb_id)
        for m in _movie_list(r):
            if _normalize_imdb(m.get("imdbId")) == want:
                return m
    return None


def radarr_delete_movie(instance: dict, movie_id, delete_files: bool = True) -> bool:
    """Delete a movie from a Radarr instance (entry and optionally files on disk)."""
    url = f"{instance['url']}/api/v3/movie/{movie_id}"
    params = {
        "apikey": instance["api_key"],
        "deleteFiles": "true" if delete_files else "false",
        "addImportExclusion": "false",
    }
    r = requests.delete(url, params=params, timeout=15)
    r.raise_for_status()
    return True
