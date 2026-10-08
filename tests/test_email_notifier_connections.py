"""Run: python tests/test_email_notifier_connections.py   (plain asserts: no database, no network, no mail).

find_best_fit_tenders_for_user() shares ONE cursor with its caller, and the caller keeps using it
afterwards: the alerted-tender lookup, record_sent_alerts() and mark_digest_sent(). The
saved_search_cache helpers it calls open AND CLOSE whatever connection their get_db_conn_fn
returns, so they must be given a factory that opens a new connection -- never `lambda: cursor.connection`.
That lambda closed the caller's connection mid-job, so every later query failed with "cursor already
closed", nothing was recorded as alerted, and the same tenders were emailed again on every run.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import etenders_scraper.sources as srcs  # noqa: E402
import tender_app.db as dbmod  # noqa: E402
import tender_app.email_notifier as en  # noqa: E402
import tender_app.saved_search_cache as ssc  # noqa: E402


class InterfaceError(Exception):
    """What psycopg2 raises when a closed connection's cursor is used."""


class FakeConn:
    def __init__(self, name):
        self.name = name
        self.closed = 0

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = 1

    def commit(self):
        pass

    def rollback(self):
        pass


class FakeCursor:
    """Answers just enough of the queries the notifier makes for it to find search keywords."""

    def __init__(self, conn):
        self.connection = conn
        self._rows = []

    def execute(self, sql, params=None):
        if self.connection.closed:
            raise InterfaceError("cursor already closed")
        s = " ".join(sql.split())
        if re.search(r"SELECT id FROM saved_searches", s):
            self._rows = [(7,)]
        elif re.search(r"SELECT id FROM recent_searches", s):
            self._rows = [(9,)]
        elif re.search(r"SELECT query, scope FROM (saved|recent)_searches", s):
            self._rows = [("zzprobe", "contracts_finder")]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def check_shared_connection_survives(notifier=en):
    """Run the candidate search with cache helpers that honour the real contract (open the
    connection they are handed, then close it) and return (shared_conn, connections_handed_out)."""
    handed = []

    def contract_helper(get_db_conn_fn, search_id, username=None, **kwargs):
        conn = get_db_conn_fn()
        handed.append(conn)
        conn.close()
        return {"rows": []}

    names = (
        "refresh_saved_search_cache",
        "get_cached_saved_search_results",
        "refresh_recent_search_cache",
        "get_cached_recent_search_results",
    )
    saved = {n: getattr(ssc, n) for n in names}
    real_conn_factory, real_search = dbmod.get_db_connection, srcs.search_single_source
    try:
        for n in names:
            setattr(ssc, n, contract_helper)
        dbmod.get_db_connection = lambda *a, **k: FakeConn("own")
        srcs.search_single_source = lambda *a, **k: ([], {})  # no portal is contacted

        shared = FakeConn("shared")
        cursor = shared.cursor()
        notifier.find_best_fit_tenders_for_user(cursor, "probe-user")
        return shared, cursor, handed
    finally:
        for n, fn in saved.items():
            setattr(ssc, n, fn)
        dbmod.get_db_connection, srcs.search_single_source = real_conn_factory, real_search


def main():
    shared, cursor, handed = check_shared_connection_survives()
    assert not shared.closed, "the caller's connection was closed during the candidate search"
    assert all(c is not shared for c in handed), "a cache helper was handed the caller's own connection"
    assert len(handed) == 4, f"expected the saved- and recent-search helpers to run twice each, got {len(handed)}"
    cursor.execute("SELECT 1")  # raises InterfaceError if the caller's cursor was closed under it
    print("OK: the saved- and recent-search cache helpers get their own connections; the shared one stays open")


if __name__ == "__main__":
    main()
