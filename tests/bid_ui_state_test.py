"""Run: python tests/bid_ui_state_test.py
Release 13: the per-bid state kept on the server (typed prices, typed "Things to confirm" values) and the readiness link to the list."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tender_app.bid_ui_state import clean_state, load_state, save_state  # noqa: E402
from tender_app.readiness import readiness  # noqa: E402

n = 0


def check(cond, msg):
    global n
    assert cond, msg
    n += 1


class Cur:
    def __init__(self, db): self.db, self.row = db, None
    def execute(self, sql, args=()):
        if sql.startswith("SELECT"): self.row = (self.db["v"],) if "v" in self.db else None
        else: self.db["v"] = args[2]
    def fetchone(self): return self.row
    def close(self): pass


class Conn:
    def __init__(self): self.db = {}
    def cursor(self): return Cur(self.db)
    def commit(self): pass


# only known keys, short strings, empty values dropped
c = clean_state({"prices": {"prices": {"0:1": "12.50"}, "text": "a: 1"}, "todo_drafts": {"[X]": "v", "[Y]": "  "}, "evil": {"a": 1}})
check(set(c) == {"prices", "todo_drafts"}, "unknown keys are dropped")
check(c["todo_drafts"] == {"[X]": "v"}, "empty drafts are dropped")
check(clean_state("junk") == {} and clean_state({"prices": 5}) == {}, "bad shapes give nothing")
check(len(clean_state({"todo_drafts": {str(i): "v" for i in range(500)}})["todo_drafts"]) == 100, "the list is capped")

# a save merges: saving drafts keeps the prices already stored, and a second user's key does not collide
conn = Conn()
save_state(conn, "u", "bid1", {"prices": {"prices": {"0:0": "5"}, "text": ""}})
out = save_state(conn, "u", "bid1", {"todo_drafts": {"[A]": "x"}})
check(out["prices"]["prices"] == {"0:0": "5"} and out["todo_drafts"] == {"[A]": "x"}, "keys are merged")
check(load_state(conn, "u", "bid1") == out, "what was saved is read back")
check(json.loads(conn.db["v"])["todo_drafts"] == {"[A]": "x"}, "stored as JSON")
save_state(conn, "u", "bid1", {"todo_drafts": {}})
check(load_state(conn, "u", "bid1")["todo_drafts"] == {}, "clearing the drafts clears them")


class Broken:
    def cursor(self): raise RuntimeError("db down")
    def commit(self): pass


check(load_state(Broken(), "u", "b") == {}, "a database problem reads as nothing saved")
check(save_state(Broken(), "u", "b", {"todo_drafts": {"[A]": "x"}}) == {"todo_drafts": {"[A]": "x"}}, "a failed save does not raise")

# readiness: the open-gap items point at the list
bid = {"cover_letter": {"content": "Dear [NEEDS INPUT: name], we hold [CONFIRM: ISO 9001 - from a past bid]."}}
r = readiness(bid, {}, {"issues": []})
gaps = [i for i in r["items"] if i["check"] in ("placeholders", "reused_facts")]
check(gaps and all(i["goto"] == "Things to confirm" for i in gaps), "open-gap items go to the list: %s" % gaps)
print("bid ui state: %d checks passed" % n)
