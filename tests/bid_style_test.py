"""Release 8 checks: per-bid past-bid choice, reuse rules, anonymised answers, branding switch. Run: python tests/bid_style_test.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tender_app import bid_style as bs

n = 0


def ok(c, m):
    global n
    n += 1
    assert c, "FAILED: " + m


LIB = [
    {"id": 1, "title": "Matthewsgreen Community Centre operator", "buyer": "Wokingham Borough Council", "year": "2025",
     "outcome": "won", "text": "Safeguarding and equality, diversity and inclusion. We delivered GBP 2,500 sinking fund and 94% satisfaction in 2024."},
    {"id": 2, "title": "Library services contract", "buyer": "Kent", "year": "2023", "outcome": "lost", "text": "Library opening hours."},
    {"id": 3, "title": "Community centre management", "buyer": "Bath", "year": "2022", "outcome": "unknown", "text": "Community centre bookings."},
]
ok(bs.normalise_outcome("Win") == "won" and bs.normalise_outcome("") == "unknown" and bs.normalise_outcome("lost") == "lost", "outcomes")
ok(bs.normalise_mode(None, 3) == "auto" and bs.normalise_mode(None, 0) == "off" and bs.normalise_mode("library", 0) == "library", "default mode")
r = bs.rank_past_bids(LIB, "Live Well community centre evaluation", "community centre safeguarding")
ok(r[0]["id"] == 1, "won and on-topic ranks first: %s" % [b["id"] for b in r])
ok([b["id"] for b in bs.choose_past_bids("auto", LIB, [], "community centre", "", limit=2)][0] == 1, "auto picks closest")
ok([b["id"] for b in bs.choose_past_bids("library", LIB, [3, 99], "x", "")] == [3], "library keeps the ticks only")
ok(bs.choose_past_bids("off", LIB, [1], "x", "") == [], "off uses nothing")
blk = bs.past_bids_block([LIB[0], LIB[1]])
ok("[CONFIRM: <value> - from a past bid]" in blk and "[the buyer]" in blk, "rules in the prompt")
ok("Reuse no wording" in blk and "Library opening hours" not in blk, "a lost bid gives no text")
t, c = bs.mark_reused_facts("We hold a GBP 2,500 fund and 94% satisfaction in 2024.", LIB[0]["text"], "our own data: 2024", ("Wokingham Borough Council",))
ok(c == 2 and "[CONFIRM: 94% - from a past bid]" in t and "[CONFIRM: GBP 2,500" in t and "2024]" not in t.replace("in 2024.", ""), "past-only facts marked, own-evidence fact kept: %s" % t)
t, c = bs.mark_reused_facts("Wokingham Borough Council asked for it.", LIB[0]["text"], "", ("Wokingham Borough Council",))
ok("[the buyer]" in t and c == 1, "old buyer name replaced")
t2, c2 = bs.mark_reused_facts(t, LIB[0]["text"], "")
ok(c2 == 0, "marking twice does nothing")
ok(bs.asks_for_anonymised_answers("Quality answers must be anonymised and must not identify the bidder."), "anonymise asked")
ok(not bs.asks_for_anonymised_answers("Describe your approach to quality."), "not asked")
ok(bs.resolve_branding(None, True, False) == {"enabled": True, "answers": True, "cover_and_pricing": True}, "default follows the brand kit")
ok(bs.resolve_branding(True, True, True) == {"enabled": True, "answers": False, "cover_and_pricing": True}, "anonymise overrides answers only")
ok(bs.resolve_branding(True, False, False)["enabled"] is False, "no kit, no branding")
s = bs.per_bid_settings({"past_bid_ids": [1, 2, 3, 4, 5, 6]}, 3, True)
ok(s["past_bids_mode"] == "auto" and len(s["past_bid_ids"]) == 5 and s["branding_enabled"] is True, "settings default from the profile: %s" % s)
# ---- release 8: the buyer's format rules switch branding off ---------------------------------------------------------------
r = bs.branding_rules({"other": ["Use Arial 10 or larger"]}, "")
ok(r["auto_off"] and r["set_font"] and "sets the font" in r["reason"] and "Switch it back on" in r["reason"], "set fonts switch branding off with a reason")
r = bs.branding_rules({}, "Quality answers must be anonymised and must not identify the bidder.")
ok(r["auto_off"] and r["anonymise"], "anonymised answers switch branding off")
r = bs.branding_rules({}, "Responses must be submitted using the attached response template.")
ok(r["auto_off"] and r["buyer_template"], "a buyer template switches branding off")
r = bs.branding_rules({"structure": "sections 1-6"}, "Describe your approach to delivery.")
ok(not r["auto_off"] and r["reason"] == "", "no format rule: branding stays on")
ok(not bs.branding_rules(None, None)["auto_off"], "safe on empty input")
print("bid style: %d checks passed" % n)
