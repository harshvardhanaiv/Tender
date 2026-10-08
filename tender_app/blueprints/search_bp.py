"""Search blueprint — registers search/tender/export routes from server module.

Search and AI routes remain in server.create_app due to tight coupling with
in-app DeepSeek helpers; this blueprint provides the registration hook and
documents the boundary for future extraction.
"""
from __future__ import annotations

from flask import Blueprint

search_bp = Blueprint("search", __name__)


def init_search_blueprint(app, register_fn):
    """register_fn(app) attaches /api/search, /api/tender/*, /api/download routes."""
    register_fn(app)
    return search_bp


def init_ai_blueprint(app, register_fn):
    """register_fn(app) attaches AI routes (/api/fit-score, /api/analyse, etc.)."""
    register_fn(app)
    from flask import Blueprint
    ai_bp = Blueprint("ai", __name__)
    return ai_bp
