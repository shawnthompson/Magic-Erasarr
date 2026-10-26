"""Main (UI) routes."""
from urllib.parse import urlsplit

from flask import Blueprint, render_template, request

from config import DEBUG, GITHUB_REPO, VERSION

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("index.html", version=VERSION, github_repo=GITHUB_REPO, debug=DEBUG)


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
