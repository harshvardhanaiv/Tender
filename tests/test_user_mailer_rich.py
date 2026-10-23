"""Run: python tests/test_user_mailer_rich.py   (plain asserts, no network, no database)

The message the user's own email account sends (tender_app/user_mailer.py): plain text, text plus HTML, and attached files.
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import user_mailer as um  # noqa: E402

ACCOUNT = {"from_email": "me@own.example", "from_name": "Sam", "reply_to": None}


def test_a_plain_message_is_still_plain():
    msg = um.build_message(ACCOUNT, "buyer@x.example", "Subject", "Hello")
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content().strip() == "Hello"
    assert msg["List-Unsubscribe"].startswith("<mailto:me@own.example")


def test_html_travels_beside_the_text():
    msg = um.build_message(ACCOUNT, "buyer@x.example", "Subject", "Hello there", html="<p>Hello <b>there</b></p>")
    assert msg.get_content_type() == "multipart/alternative"
    kinds = [p.get_content_type() for p in msg.iter_parts()]
    assert kinds == ["text/plain", "text/html"]
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "<b>there</b>" in html and html.startswith("<!doctype html>")
    assert msg.get_body(preferencelist=("plain",)).get_content().strip() == "Hello there"


def test_files_are_attached_with_their_names_and_types():
    files = [{"filename": "Case study.pdf", "content_type": "application/pdf", "data": b"%PDF-1.4 x"},
             {"filename": "notes.txt", "content_type": "text/plain", "data": b"hi"}]
    msg = um.build_message(ACCOUNT, "buyer@x.example", "Subject", "Hello", html="<p>Hello</p>", attachments=files)
    assert msg.get_content_type() == "multipart/mixed"
    got = [(a.get_filename(), a.get_content_type(), a.get_content()) for a in msg.iter_attachments()]
    assert got[0] == ("Case study.pdf", "application/pdf", b"%PDF-1.4 x")
    assert got[1][0] == "notes.txt" and got[1][1] == "text/plain"
    assert msg.get_body(preferencelist=("html",)) is not None  # the formatted body is still the message


def test_files_on_a_plain_message():
    msg = um.build_message(ACCOUNT, "b@x.example", "S", "Hello", attachments=[{"filename": "a.pdf", "content_type": "application/pdf", "data": b"x"}])
    assert msg.get_content_type() == "multipart/mixed"
    assert [a.get_filename() for a in msg.iter_attachments()] == ["a.pdf"]


def test_a_type_with_no_subtype_still_attaches():
    msg = um.build_message(ACCOUNT, "b@x.example", "S", "Hello", attachments=[{"filename": "a.bin", "content_type": "weird", "data": b"x"}])
    assert [a.get_content_type() for a in msg.iter_attachments()] == ["weird/octet-stream"]


class _Smtp:
    def __init__(self):
        self.sent = []

    def login(self, user, password):
        pass

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        pass


def test_send_batch_passes_the_html_and_files_to_each_message():
    smtp = _Smtp()
    items = [{"key": 1, "to": "a@x.example", "subject": "S", "body": "Hello A", "html": "<p><b>A</b></p>",
              "attachments": [{"filename": "a.pdf", "content_type": "application/pdf", "data": b"x"}]},
             {"key": 2, "to": "b@x.example", "subject": "S", "body": "Hello B"}]
    res = um.send_batch({**ACCOUNT, "host": "h", "port": 25, "security": "none", "login": "l"}, "pw", items, factory=lambda *a, **k: smtp, pause=0)
    assert [r["ok"] for r in res] == [True, True]
    first, second = smtp.sent
    assert first.get_content_type() == "multipart/mixed" and first.get_body(preferencelist=("html",)) is not None
    assert second.get_content_type() == "text/plain"


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print("ok   ", name)
        except Exception:  # noqa: BLE001
            failed += 1
            print("FAIL ", name)
            traceback.print_exc()
    print(f"\n{len(tests) - failed} of {len(tests)} passed")
    sys.exit(1 if failed else 0)
