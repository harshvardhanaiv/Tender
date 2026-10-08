"""Rate limiting, CSRF, and security helpers."""
from __future__ import annotations

import secrets
import time
from collections import defaultdict
from functools import wraps
from threading import Lock
from typing import Callable

from flask import jsonify, request, session

_lock = Lock()
_buckets: dict[str, list[float]] = defaultdict(list)


def _client_key(prefix: str) -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    if "," in ip:
        ip = ip.split(",")[0].strip()
    user = session.get("username", "")
    return f"{prefix}:{user or ip}"


def rate_limit(max_calls: int, window_sec: int = 60) -> Callable:
    """Simple in-memory rate limiter per user/IP."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = _client_key(fn.__name__)
            now = time.time()
            with _lock:
                hits = _buckets[key]
                _buckets[key] = [t for t in hits if now - t < window_sec]
                if len(_buckets[key]) >= max_calls:
                    return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
                _buckets[key].append(now)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def ensure_csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf() -> bool:
    """Validate CSRF on mutating requests. Skip for webhooks."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    if request.path.startswith("/api/stripe/webhook"):
        return True
    if request.path.startswith("/api/auth/firebase"):
        return True
    if request.path.startswith("/api/auth/forgot-password"):
        return True
    token = request.headers.get("X-CSRF-Token") or request.args.get("csrf_token") or ""
    if not token and request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            token = body.get("csrf_token") or ""
        except Exception:
            pass
    return bool(token and token == session.get("csrf_token"))


def csrf_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not validate_csrf():
            return jsonify({"error": "Invalid CSRF token"}), 403
        return fn(*args, **kwargs)

    return wrapper
