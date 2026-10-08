#!/usr/bin/env python3
"""Rebuild supplier_stats and buyer_stats (the precomputed aggregates behind Supplier and Buyer
Intelligence). The app also does this itself in the background when the stats are older than
six hours; run this after a big load or a merge/cleanup to refresh immediately.

    python scripts/refresh_stats.py

Safe to run while the app is serving: readers keep seeing the old rows until the new ones commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app.db import get_db_connection  # noqa: E402
from tender_app.stats import refresh_all  # noqa: E402

if __name__ == "__main__":
    print(refresh_all(get_db_connection))
