"""Application configuration from environment."""
import os

from dotenv import load_dotenv

load_dotenv()


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("true", "1", "yes")


DEBUG = _bool_env("DEBUG", False)
STAT = _bool_env("STAT", True)

VERSION = "1.7.0"
GITHUB_REPO = "https://github.com/cbodden/Magic-Erasarr"


def _build_arr_instances(prefix: str, count: int = 2) -> list[dict]:
    """Read numbered *arr instance configs from env vars.

    E.g. prefix="RADARR" reads RADARR_1_URL, RADARR_1_API_KEY, RADARR_1_NAME,
    then RADARR_2_*, etc.  Instances with a blank URL are skipped.
    """
    instances = []
    for i in range(1, count + 1):
        url = os.getenv(f"{prefix}_{i}_URL", "").rstrip("/")
        key = os.getenv(f"{prefix}_{i}_API_KEY", "")
        name = os.getenv(f"{prefix}_{i}_NAME", f"{prefix} {i}")
        if url and key:
            instances.append({"url": url, "api_key": key, "name": name})
    return instances


TAUTULLI_URL = os.getenv("TAUTULLI_URL", "http://localhost:8181").rstrip("/")
TAUTULLI_API_KEY = os.getenv("TAUTULLI_API_KEY", "")

# Optional: Plex Media Server (to refresh library after Radarr deletes files)
PLEX_URL = os.getenv("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "")

OVERSEERR_URL = os.getenv("OVERSEERR_URL", "http://localhost:5055").rstrip("/")
OVERSEERR_API_KEY = os.getenv("OVERSEERR_API_KEY", "")

RADARR_INSTANCES = _build_arr_instances("RADARR")
SONARR_INSTANCES = _build_arr_instances("SONARR")
LIDARR_INSTANCES = _build_arr_instances("LIDARR")
