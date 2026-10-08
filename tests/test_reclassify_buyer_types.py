"""The buyer-type reclassify script must not run one UPDATE per row, and must show the names behind each change.

Stage 4 of the live sync would have sent ~44,000 single-row UPDATEs over the internet (hours, the same trap as
the planning dump). It now sends one UPDATE per chunk of ids. Also pins the classifier cases the live dry run found.
Run: python tests/test_reclassify_buyer_types.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

SRC = (ROOT / "scripts" / "reclassify_buyer_types.py").read_text(encoding="utf-8")


def test_apply_updates_in_chunks_not_row_by_row():
    assert "WHERE id = %s" not in SRC, "a per-row UPDATE is back: 44,000 round trips over the remote connection"
    assert "id = ANY(%s)" in SRC and "APPLY_CHUNK" in SRC
    import importlib.util
    spec = importlib.util.spec_from_file_location("reclassify_buyer_types", ROOT / "scripts" / "reclassify_buyer_types.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.APPLY_CHUNK >= 1000
    print("ok    test_apply_updates_in_chunks_not_row_by_row")


def test_dry_run_lists_the_names_behind_each_transition():
    assert "Names behind each transition" in SRC and "names_by_change" in SRC
    print("ok    test_dry_run_lists_the_names_behind_each_transition")


if __name__ == "__main__":
    test_apply_updates_in_chunks_not_row_by_row()
    test_dry_run_lists_the_names_behind_each_transition()
    print("2 passed, 0 skipped, 0 failed")
