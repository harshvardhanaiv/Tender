"""Keeps the last completed Contracts Finder feed scan in the app database.

The scan takes minutes and the production container has no persistent volume, so without this
every redeploy or restart began with an empty cache and the first searches waited on a fresh
scan (see contracts_finder.start_feed_prewarm)."""
from __future__ import annotations

import gzip
import io
import json
from typing import Any, Callable

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS cf_feed_snapshot (
    cache_key  TEXT PRIMARY KEY,
    version    INTEGER NOT NULL,
    scanned_at DOUBLE PRECISION NOT NULL,
    payload    BYTEA NOT NULL
)
"""

_UPSERT_SQL = """
INSERT INTO cf_feed_snapshot (cache_key, version, scanned_at, payload)
VALUES (%s, %s, %s, %s)
ON CONFLICT (cache_key) DO UPDATE
SET version = EXCLUDED.version, scanned_at = EXCLUDED.scanned_at, payload = EXCLUDED.payload
"""


def encode_entries(entries: list[tuple[str, dict[str, Any]]]) -> bytes:
    """gzip'd JSON lines, one [search_text, row] per line, so neither side builds one giant string."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=5) as gz:
        for search_text, row in entries:
            gz.write(json.dumps([search_text, row], ensure_ascii=False).encode("utf-8") + b"\n")
    return buf.getvalue()


def decode_entries(payload: bytes) -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as gz:
        for line in gz:
            search_text, row = json.loads(line)
            entries.append((search_text, row))
    return entries


class DbFeedStore:
    def __init__(self, get_db_connection: Callable[[], Any]):
        self._get_db = get_db_connection

    def save(self, key: str, version: int, scanned_at: float, entries: list[tuple[str, dict[str, Any]]]) -> None:
        payload = encode_entries(entries)
        conn = self._get_db()
        try:
            cur = conn.cursor()
            cur.execute(_CREATE_SQL)
            cur.execute(_UPSERT_SQL, (key, version, scanned_at, payload))
            conn.commit()
        finally:
            conn.close()

    def load(self, key: str, version: int) -> tuple[float, list[tuple[str, dict[str, Any]]]] | None:
        conn = self._get_db()
        try:
            cur = conn.cursor()
            cur.execute(_CREATE_SQL)
            conn.commit()
            cur.execute(
                "SELECT scanned_at, payload FROM cf_feed_snapshot WHERE cache_key = %s AND version = %s",
                (key, version),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        scanned_at, payload = row
        return float(scanned_at), decode_entries(bytes(payload))
