"""Run: python tests/test_email_rich.py   (plain asserts, no network, no database)

The formatted email body (tender_app/email_rich.py): what the editor may send, the plain-text twin, per-buyer merge, attachment rules.
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_app import email_rich as er  # noqa: E402


def test_keeps_the_allowed_formatting():
    html = '<p>Hello <b>there</b>, <i>you</i> and <u>us</u>.</p><ul><li>One</li><li>Two</li></ul><h2>Head</h2>'
    assert er.sanitize_html(html) == html


def test_scripts_styles_events_and_images_are_removed():
    dirty = ('<p onclick="x()" style="color:red">Hi<script>alert(1)</script></p><style>p{}</style>'
             '<img src=x onerror=alert(2)><iframe src="http://evil"></iframe><form action="http://evil"><input></form>')
    out = er.sanitize_html(dirty)
    for bad in ("script", "alert", "onclick", "style", "<img", "onerror", "iframe", "<form", "<input", "evil"):
        assert bad not in out, (bad, out)
    assert out == "<p>Hi</p>"


def test_only_web_and_mail_links_survive():
    assert er.sanitize_html('<p><a href="https://aivhub.com/x?a=1&b=2" target="_blank" onclick="x">site</a></p>') == \
        '<p><a href="https://aivhub.com/x?a=1&amp;b=2">site</a></p>'
    assert er.sanitize_html('<p><a href="mailto:a@b.example">mail</a></p>') == '<p><a href="mailto:a@b.example">mail</a></p>'
    assert er.sanitize_html('<p><a href="javascript:alert(1)">click</a></p>') == "<p>click</p>"
    assert er.sanitize_html('<p><a href="data:text/html;base64,AAAA">click</a></p>') == "<p>click</p>"
    assert er.sanitize_html('<p><a href=" JaVaScRiPt:alert(1)">click</a></p>') == "<p>click</p>"
    assert er.sanitize_html('<p><a>no address</a></p>') == "<p>no address</p>"


def test_div_becomes_paragraph_and_broken_html_is_closed():
    assert er.sanitize_html("<div>One</div><div>Two</div>") == "<p>One</p><p>Two</p>"
    assert er.sanitize_html("<p>Open <b>bold") == "<p>Open <b>bold</b></p>"
    assert er.sanitize_html("<b>a</i>b</b>") == "<b>ab</b>"


def test_nothing_visible_is_empty_and_non_text_is_empty():
    assert er.sanitize_html("") == ""
    assert er.sanitize_html("<p> </p><br>") == ""
    assert er.sanitize_html(None) == ""
    assert er.sanitize_html(5) == ""
    assert er.sanitize_html("<script>x</script>") == ""


def test_merge_fields_survive_the_cleaner_and_text_is_escaped():
    out = er.sanitize_html("<p>Hello {{buyer_contact}}, <b>{{buyer_name}}</b> &amp; co</p>")
    assert "{{buyer_contact}}" in out and "{{buyer_name}}" in out
    assert "&amp; co" in out
    assert "&lt;" in er.sanitize_html("<p>1 &lt; 2</p>")


def test_text_version():
    html = '<p>Hello <b>there</b>.</p><p>Line<br>two</p><ul><li>One</li><li>Two</li></ul><p><a href="https://x.example/a">our site</a></p>'
    assert er.html_to_text(html) == "Hello there.\n\nLine\ntwo\n\n\n- One\n- Two\n\nour site (https://x.example/a)".replace("\n\n\n- One", "\n\n- One")
    assert er.html_to_text('<p><a href="https://x.example/">https://x.example</a></p>') == "https://x.example"
    assert er.html_to_text('<p>5 &amp; 6 &lt; 7</p>') == "5 & 6 < 7"


def test_text_to_html_and_back():
    text = "Hello,\n\nWe saw <this> & that.\nSecond line.\n\nKind regards,\nSam"
    html = er.text_to_html(text)
    assert html == "<p>Hello,</p><p>We saw &lt;this&gt; &amp; that.<br>Second line.</p><p>Kind regards,<br>Sam</p>"
    assert er.html_to_text(html) == text
    assert er.text_to_html("") == ""


def test_render_html_fills_one_buyer_and_escapes_values():
    html = "<p>Hello {{buyer_contact}}, <b>{{buyer_name}}</b> {{other}}</p>"
    out = er.render_html(html, {"buyer_contact": "team", "buyer_name": 'A & B <Ltd> "x"'})
    assert out == "<p>Hello team, <b>A &amp; B &lt;Ltd&gt; &quot;x&quot;</b> {{other}}</p>"
    assert "<Ltd>" not in out


def test_has_formatting():
    assert not er.has_formatting("<p>Plain</p><p>text<br>here</p>")
    assert not er.has_formatting("")
    assert not er.has_formatting(None)
    assert er.has_formatting("<p>a <b>b</b></p>")
    assert er.has_formatting("<p><a href=\"https://x.example\">x</a></p>")


def test_a_very_long_message_is_cut_cleanly():
    html = "<p>" + ("word " * 20) + "</p>"
    out = er.sanitize_html(html * 600)
    assert len(out) <= er.MAX_HTML
    assert out.endswith("</p>") and out.count("<p>") == out.count("</p>")


def test_attachment_rules():
    assert er.attachment_problem("brochure.pdf", 100_000, []) is None
    assert "block" in er.attachment_problem("setup.exe", 10, [])
    assert "block" in er.attachment_problem("macro.docm", 10, [])
    assert "empty" in er.attachment_problem("a.pdf", 0, [])
    assert "larger" in er.attachment_problem("a.pdf", er.MAX_FILE_BYTES + 1, [])
    assert "at most" in er.attachment_problem("a.pdf", 10, [{"size_bytes": 1}] * er.MAX_FILES)
    assert "together" in er.attachment_problem("a.pdf", 6 * 1024 * 1024, [{"size_bytes": 5 * 1024 * 1024}])
    assert er.attachment_problem("", 10, []) == "The file has no name."


def test_file_names_and_types():
    assert er.safe_filename("C:\\Users\\me\\Case study.pdf") == "Case study.pdf"
    assert er.safe_filename("../../etc/passwd") == "passwd"
    assert er.safe_filename('a<b>"c".pdf') == "abc.pdf"
    assert er.safe_filename(None) == ""
    assert er.content_type_for("a.pdf") == "application/pdf"
    assert er.content_type_for("noext", "text/plain") == "text/plain"
    assert er.content_type_for("noext", "bad type!") == "application/octet-stream"


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
