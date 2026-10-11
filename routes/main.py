"""Main (UI) routes."""
from flask import Blueprint, render_template

from config import DEBUG, GITHUB_REPO, VERSION

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("index.html", version=VERSION, github_repo=GITHUB_REPO, debug=DEBUG)
