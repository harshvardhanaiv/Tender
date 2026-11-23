"""Release 13: what a user typed on one bid's page, kept on the server.

Two things only: the unit prices typed in "Your prices" and the values typed (but not yet put in the draft) in
"Things to confirm". Both used to live in one browser's storage. A save merges the keys it is given into what is
already stored, trims everything to a known shape and size, and never raises into the page.
"""
import json

MAX_TODO = 100
MAX_PRICES = 300
MAX_TEXT = 20000


def _s(v, n):
    return str(v if v is not None else "")[:n]


def clean_state(state):
    """Only the known keys, with strings of a sane length. Anything else is dropped."""
    out = {}
    if not isinstance(state, dict):
        return out
    pr = state.get("prices")
    if isinstance(pr, dict):
        prices = pr.get("prices") if isinstance(pr.get("prices"), dict) else {}
        out["prices"] = {"prices": {_s(k, 20): _s(v, 40) for k, v in list(prices.items())[:MAX_PRICES]},
                         "text": _s(pr.get("text"), MAX_TEXT)}
    td = state.get("todo_drafts")
    if isinstance(td, dict):
        out["todo_drafts"] = {_s(k, 300): _s(v, 300) for k, v in list(td.items())[:MAX_TODO] if _s(v, 300).strip()}
    return out


def load_state(conn, username, bid_key):
    try:
        cur = conn.cursor()
        cur.execute("SELECT state_json FROM bid_ui_state WHERE username = %s AND bid_key = %s", (username, bid_key))
        row = cur.fetchone()
        cur.close()
        return clean_state(json.loads(row[0])) if row and row[0] else {}
    except Exception as e:  # noqa: BLE001 - a read problem means "nothing saved", never an error on the page
        print("[bid ui state] not loaded:", e)
        return {}


def save_state(conn, username, bid_key, new):
    merged = load_state(conn, username, bid_key)
    merged.update(clean_state(new))
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bid_ui_state (username, bid_key, state_json, updated_at) VALUES (%s,%s,%s,CURRENT_TIMESTAMP) "
            "ON CONFLICT (username, bid_key) DO UPDATE SET state_json = EXCLUDED.state_json, updated_at = CURRENT_TIMESTAMP",
            (username, bid_key, json.dumps(merged)))
        conn.commit()
        cur.close()
    except Exception as e:  # noqa: BLE001
        print("[bid ui state] not saved:", e)
    return merged
