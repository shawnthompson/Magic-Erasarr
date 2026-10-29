"""Main (UI) routes."""
from urllib.parse import urlsplit

from flask import Blueprint, render_template, request

from config import (
    DEBUG,
    GITHUB_REPO,
    LIDARR_INSTANCES,
    OVERSEERR_API_KEY,
    OVERSEERR_URL,
    PLEX_TOKEN,
    PLEX_URL,
    RADARR_INSTANCES,
    SONARR_INSTANCES,
    TAUTULLI_API_KEY,
    TAUTULLI_URL,
    VERSION,
)

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("index.html", version=VERSION, github_repo=GITHUB_REPO, debug=DEBUG)


def _integration_status():
    """Return safe, non-secret integration information for the settings page."""
    integrations = [
        {"name": "Tautulli", "url": TAUTULLI_URL, "configured": bool(TAUTULLI_URL and TAUTULLI_API_KEY)},
        {"name": "Seerr", "url": OVERSEERR_URL, "configured": bool(OVERSEERR_URL and OVERSEERR_API_KEY)},
        {"name": "Plex", "url": PLEX_URL or "Optional", "configured": bool(PLEX_URL and PLEX_TOKEN)},
    ]
    for instances in (RADARR_INSTANCES, SONARR_INSTANCES, LIDARR_INSTANCES):
        for instance in instances:
            integrations.append({
                "name": instance["name"],
                "url": instance["url"],
                "configured": True,
            })
    return integrations


@main_bp.route("/settings")
def settings():
    return render_template(
        "settings.html",
        version=VERSION,
        github_repo=GITHUB_REPO,
        integrations=_integration_status(),
    )


def _safe_return_url(value):
    """Accept only same-origin relative paths for the detail page back link."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


@main_bp.route("/item/<rating_key>")
def item_detail(rating_key):
    return render_template(
        "item.html",
        rating_key=rating_key,
        return_url=_safe_return_url(request.args.get("return")),
        version=VERSION,
        github_repo=GITHUB_REPO,
        debug=DEBUG,
    )
