"""Run: python tests/test_llm_json.py   (plain asserts, no test framework needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tender_app.llm_json import parse_llm_json  # noqa: E402

CASES = [
    ("plain object", '{"score": 72, "gaps": ["a", "b"]}', {"score": 72, "gaps": ["a", "b"]}),
    ("json fence", '```json\n{"score": 72}\n```', {"score": 72}),
    ("bare fence", '```\n{"score": 72}\n```', {"score": 72}),
    ("prose around", 'Sure! Here is the result:\n{"score": 72}\nHope that helps.', {"score": 72}),
    ("array root", 'Result: [1, 2, 3] done', [1, 2, 3]),
    ("raw newline in string", '{"text": "line one\nline two"}', {"text": "line one\nline two"}),
    ("tab in string", '{"text": "a\tb"}', {"text": "a\tb"}),
    ("truncated mid-string", '{"a": 1, "text": "this got cut o', {"a": 1, "text": "this got cut o"}),
    ("truncated mid-list", '{"phases": [{"label": "A"}, {"label": "B"}, {"lab', {"phases": [{"label": "A"}, {"label": "B"}]}),
    ("truncated after comma", '{"a": 1, "b": [1, 2,', {"a": 1, "b": [1, 2]}),
    ("escaped quote kept", '{"q": "say \\"hi\\""}', {"q": 'say "hi"'}),
    ("nested braces in string", '{"t": "use {x} and [y]"}', {"t": "use {x} and [y]"}),
]
failed = 0
for name, raw, expected in CASES:
    try:
        got = parse_llm_json(raw)
    except Exception as exc:  # noqa: BLE001
        got = f"EXC {exc}"
    ok = got == expected
    failed += not ok
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"\n   expected {expected!r}\n   got      {got!r}"))
for bad in ("", "   ", "no json here at all", None):
    try:
        parse_llm_json(bad)
        print("FAIL should raise:", repr(bad)); failed += 1
    except ValueError:
        print("PASS raises on", repr(bad))
sys.exit(1 if failed else 0)
