"""PostgreSQL connection factory — the only database TenderFlow uses."""
from __future__ import annotations

import os

import psycopg2


def get_db_connection(connect_timeout: int = 10):
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        database=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        connect_timeout=connect_timeout,
    )
