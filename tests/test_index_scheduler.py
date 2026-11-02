"""The index scheduler keeps PCS, Contracts Finder and Find a Tender fresh, and each can be switched off."""
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import tender_index as ti  # noqa: E402


def _started(env):
    names = []
    real_thread, saved = threading.Thread, {k: os.environ.get(k) for k in ("INDEX_PCS_ENABLED", "INDEX_OCDS_ENABLED")}

    class Fake:
        def __init__(self, target=None, daemon=None, name=None):
            names.append(name)

        def start(self):
            pass

    threading.Thread = Fake
    try:
        for k in saved:
            os.environ.pop(k, None)
        os.environ.update(env)
        ti.start_scheduler(lambda: None)
    finally:
        threading.Thread = real_thread
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return sorted(names)


def test_all_three_portals_are_kept_fresh_by_default():
    assert _started({}) == ["ContractsFinderIndex", "FindTenderIndex", "PCSIndex"]


def test_each_group_can_be_switched_off():
    assert _started({"INDEX_OCDS_ENABLED": "0"}) == ["PCSIndex"]
    assert _started({"INDEX_PCS_ENABLED": "0"}) == ["ContractsFinderIndex", "FindTenderIndex"]
    assert _started({"INDEX_PCS_ENABLED": "0", "INDEX_OCDS_ENABLED": "0"}) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok   ", name)
