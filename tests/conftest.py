"""Pytest fixtures."""
import os

import pytest


@pytest.fixture
def app():
    """Create app with test-friendly env (no external services required for basic routes)."""
    os.environ.setdefault("TAUTULLI_API_KEY", "")
    os.environ.setdefault("OVERSEERR_API_KEY", "")
    from app import create_app
    from routes import api
    api._tautulli_users_cache = None
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()
