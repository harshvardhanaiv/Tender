"""Re-test 8 G1: a document whose file is gone shows a plain message in the viewer, JSON for API callers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flask import Flask
from tender_app.blueprints.profiles_bp import _missing_file_response


def _call(accept):
    app = Flask(__name__)
    with app.test_request_context("/x", headers={"Accept": accept}):
        r = _missing_file_response()
        if isinstance(r, tuple) and isinstance(r[0], str):
            return r[1], r[0]
        resp, code = r
        return code, resp.get_data(as_text=True)


def test_browser_gets_a_page_and_api_gets_json():
    code, body = _call("text/html,application/xhtml+xml,*/*;q=0.8")
    assert code == 404 and "upload it again" in body and "{" not in body.split("<body")[0]
    code, body = _call("application/json")
    assert code == 404 and '"error"' in body and "upload it again" in body
    code, body = _call("*/*")
    assert code == 404


if __name__ == "__main__":
    test_browser_gets_a_page_and_api_gets_json()
    print("1 of 1 passed")
