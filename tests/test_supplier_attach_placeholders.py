import sqlite3
import pytest
from scripts.supplier_attach_placeholders import (
    clean_repaired_name,
    extract_base_key,
    find_attachments,
)
from scripts.supplier_canonicalize import merge

def test_xma_case():
    # Anchor: 1 real XMA row
    rows = [
        {"id": 3069, "company_number": "02051703", "name": "XMA LIMITED", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        # Placeholders
        {"id": 22640, "company_number": "CF330874", "name": "XMA", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 6198, "company_number": "CF337631", "name": "XMA Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 101649, "company_number": "CF147577", "name": "XMA UK Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 263901, "company_number": "CF533185", "name": "XMA Head Office", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 163788, "company_number": "GB-FTS-98712", "name": "(9) (18) (23) XMA Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 195291, "company_number": "GB-FTS-146281", "name": "XMA LIMITED - USE OTHER ACCOUNT", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    assert len(ambiguous) == 0
    assert len(planned) == 6
    for item in planned:
        assert item["anchor"]["id"] == 3069
        assert item["anchor"]["company_number"] == "02051703"

def test_cdw_case():
    # Anchors: CDW Ltd (02465350) and CDW INTERNATIONAL LIMITED (02680309)
    rows = [
        {"id": 3375, "company_number": "02465350", "name": "CDW Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 4892, "company_number": "02680309", "name": "CDW INTERNATIONAL LIMITED", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        # Placeholders
        {"id": 10651, "company_number": "CF335113", "name": "CDW Limited UK", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 263166, "company_number": "CF999001", "name": "CDW UK LTDClick", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 163785, "company_number": "GB-FTS-98710", "name": "(6) (16) (26) (42) (50) CDW Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    assert len(ambiguous) == 0
    assert len(planned) == 3
    for item in planned:
        # Must attach to CDW Ltd (02465350), NOT to CDW INTERNATIONAL LIMITED
        assert item["anchor"]["id"] == 3375
        assert item["anchor"]["company_number"] == "02465350"

def test_computacenter_case():
    # Multiple real CH numbers for COMPUTACENTER
    rows = [
        {"id": 3715, "company_number": "03110569", "name": "COMPUTACENTER", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 4082, "company_number": "01584718", "name": "COMPUTACENTER (UK) LIMITED", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 232657, "company_number": "03798091", "name": "COMPUTACENTER (FMS) LIMITED", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 239594, "company_number": "03110659", "name": "Computacenter Limited", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        # Placeholders
        {"id": 4431, "company_number": "CF323063", "name": "Computacenter (Gcat)", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 257587, "company_number": "CF248203", "name": "Computacenter Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 267780, "company_number": "CF669439", "name": "COMPUTACENTER UK LTD    United Kingdom", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    # Both Computacenter Ltd and COMPUTACENTER UK LTD resolve to base key COMPUTACENTER, which has >1 real anchor -> ambiguous!
    ambig_ids = {a["row"]["id"] for a in ambiguous}
    assert 257587 in ambig_ids
    assert 267780 in ambig_ids
    # None of these ambiguous placeholders should attach
    attached_ids = {p["placeholder"]["id"] for p in planned}
    assert 257587 not in attached_ids
    assert 267780 not in attached_ids

def test_insight_direct_case():
    # Two real numbers: Insight Direct (UK) Ltd vs Insight Direct UK Ltd
    rows = [
        {"id": 3254, "company_number": "02579852", "name": "INSIGHT DIRECT (UK) LTD", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 12832, "company_number": "04272677", "name": "Insight Direct UK Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        # Placeholder
        {"id": 4425, "company_number": "CF999002", "name": "Insight Direct (UK) Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    # Both 3254 and 12832 are real numbers; neither is ever in planned attachments
    attached_p_ids = {p["placeholder"]["id"] for p in planned}
    assert 3254 not in attached_p_ids
    assert 12832 not in attached_p_ids
    # The placeholder has 2 real anchors with base key INSIGHTDIRECT -> ambiguous!
    assert any(a["row"]["id"] == 4425 for a in ambiguous)

def test_negative_cases():
    # Company genuinely named Click or UK in real name
    rows = [
        {"id": 1, "company_number": "08889999", "name": "Click Net Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 2, "company_number": "CF111222", "name": "Click Net Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 3, "company_number": "CF333444", "name": "ABC Click Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    # Row 2 attaches to Row 1
    assert any(p["placeholder"]["id"] == 2 and p["anchor"]["id"] == 1 for p in planned)
    # Row 3 (ABC Click Ltd) does NOT lose 'Click' and does NOT attach to Click Net Ltd
    assert not any(p["placeholder"]["id"] == 3 for p in planned)

def test_wsp_multi_entity_stays_ambiguous():
    # WSP has multiple real companies: WSP Ltd (00114643) and WSP UK LIMITED (01383511)
    rows = [
        {"id": 1, "company_number": "00114643", "name": "WSP Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 2, "company_number": "01383511", "name": "WSP UK LIMITED", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        # Placeholders
        {"id": 10, "company_number": "CF1001", "name": "WSP", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 11, "company_number": "CF1002", "name": "WSP Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 12, "company_number": "CF1003", "name": "WSP UK Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    ambig_ids = {a["row"]["id"] for a in ambiguous}
    assert 10 in ambig_ids
    assert 11 in ambig_ids
    assert 12 in ambig_ids
    assert len(planned) == 0


def test_savills_multi_entity_stays_ambiguous():
    # Savills has multiple real companies: SAVILLS PLC (02122174) and SAVILLS (UK) LIMITED (02605138)
    rows = [
        {"id": 1, "company_number": "02122174", "name": "SAVILLS PLC", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 2, "company_number": "02605138", "name": "SAVILLS (UK) LIMITED", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        # Placeholders
        {"id": 20, "company_number": "CF2001", "name": "Savills", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 21, "company_number": "CF2002", "name": "Savills (UK) Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    ambig_ids = {a["row"]["id"] for a in ambiguous}
    assert 20 in ambig_ids
    assert 21 in ambig_ids
    assert len(planned) == 0


def test_aecom_multi_entity_stays_ambiguous():
    # AECOM has multiple real companies under AECOM: 01513016 and 01846493
    rows = [
        {"id": 1, "company_number": "01513016", "name": "AECOM Limited", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 2, "company_number": "01846493", "name": "AECOM LIMITED", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        # Placeholders
        {"id": 30, "company_number": "CF3001", "name": "AECOM", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 31, "company_number": "CF3002", "name": "AECOM UK Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    ambig_ids = {a["row"]["id"] for a in ambiguous}
    assert 30 in ambig_ids
    assert 31 in ambig_ids
    assert len(planned) == 0


def test_jacobs_multi_entity_stays_ambiguous():
    # Jacobs has real companies with UK and U.K.
    rows = [
        {"id": 1, "company_number": "00259504", "name": "Jacobs UK Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 2, "company_number": "02594504", "name": "Jacobs U.K Limited", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 3, "company_number": "04967961", "name": "Jacobs", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        # Placeholders
        {"id": 40, "company_number": "CF4001", "name": "Jacobs U.K Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 41, "company_number": "CF4002", "name": "Jacobs UK Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 42, "company_number": "CF4003", "name": "Jacobs", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    ambig_ids = {a["row"]["id"] for a in ambiguous}
    assert 40 in ambig_ids
    assert 41 in ambig_ids
    assert 42 in ambig_ids
    assert len(planned) == 0


def test_out_of_scope_ta():
    rows = [
        {"id": 1, "company_number": "04459448", "name": "Rosslee Construction Ltd", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
        {"id": 2, "company_number": "CF999003", "name": "R Developments Ltd T/A Rosslee Construction", "address": "", "email": "", "phone": "", "website": "", "region": "", "sme_status": "", "vcse_status": ""},
    ]
    planned, ambiguous, out_of_scope, _ = find_attachments(rows)
    # T/A row must be out of scope, not attached
    assert any(o["row"]["id"] == 2 for o in out_of_scope)
    assert not any(p["placeholder"]["id"] == 2 for p in planned)

class MockCursor:
    def __init__(self):
        self.suppliers = {
            3069: {"id": 3069, "company_number": "02051703", "name": "XMA LIMITED", "address": "Nottingham", "email": "", "phone": "", "website": "", "region": "East Midlands", "sme_status": "no", "vcse_status": "no"},
            101649: {"id": 101649, "company_number": "CF147577", "name": "XMA UK Ltd", "address": "", "email": "info@xma.co.uk", "phone": "0115", "website": "www.xma.co.uk", "region": "", "sme_status": "", "vcse_status": ""},
        }
        self.awards = [
            {"id": 1, "supplier_id": 101649, "company_number": "CF147577", "supplier_name": "XMA UK Ltd"},
            {"id": 2, "supplier_id": 3069, "company_number": "02051703", "supplier_name": "XMA LIMITED"},
        ]
        self.rowcount = 0

    def execute(self, sql, params=()):
        if "SELECT id FROM suppliers" in sql:
            val = params[0]
            ids = val if isinstance(val, (list, tuple, set)) else [val]
            self.last_result = [(sid,) for sid in ids if sid in self.suppliers]
        elif "UPDATE contract_awards" in sql:
            # params: (anchor_id, anchor_cn, p_id, p_cn, p_cn)
            surv_id = params[0]
            surv_cn = params[1]
            p_id = params[2]
            p_cn = params[3]
            count = 0
            for a in self.awards:
                if a["supplier_id"] == p_id or (p_cn and a.get("company_number") == p_cn):
                    a["supplier_id"] = surv_id
                    a["company_number"] = surv_cn
                    count += 1
            self.rowcount = count
        elif "DELETE FROM suppliers" in sql:
            val = params[0]
            ids = val if isinstance(val, (list, tuple, set)) else [val]
            for did in ids:
                self.suppliers.pop(did, None)
        elif "UPDATE suppliers SET" in sql:
            sid = params[-1]
            if sid in self.suppliers:
                self.suppliers[sid]["email"] = params[0]

    def fetchall(self):
        return self.last_result

from scripts.supplier_attach_placeholders import attach_placeholder

def test_attach_placeholder_preserves_raw_name_and_is_idempotent():
    cur = MockCursor()
    anchor = cur.suppliers[3069]
    placeholder = cur.suppliers[101649]

    # First attach
    moved = attach_placeholder(cur, anchor, placeholder)
    assert moved == 1
    # Placeholder deleted
    assert 101649 not in cur.suppliers
    # Award repointed to anchor id and company_number
    assert cur.awards[0]["supplier_id"] == 3069
    assert cur.awards[0]["company_number"] == "02051703"
    # CRITICAL: supplier_name MUST be preserved as the original raw name!
    assert cur.awards[0]["supplier_name"] == "XMA UK Ltd"
    # No orphans
    for a in cur.awards:
        assert a["supplier_id"] in cur.suppliers

    # Idempotent re-run: placeholder is already gone
    res = attach_placeholder(cur, anchor, placeholder)
    assert res == -1  # skips safely because row no longer exists


from scripts.supplier_attach_placeholders import compute_tier

def test_tier_classification():
    anchor_ltd = {"id": 1, "company_number": "01234567", "name": "ACME LIMITED"}
    anchor_plc = {"id": 2, "company_number": "07654321", "name": "ACME PLC"}

    # 1. Tier 1: clean match, same legal form, multi-word or small awards
    p_safe = {"id": 10, "company_number": "CF100", "name": "ACME Ltd", "award_count": 5}
    tier, reasons = compute_tier(p_safe, anchor_ltd, "ACME", ["direct_match"])
    assert tier == 1
    assert len(reasons) == 0

    # 2. Tier 2: rule-f (trailing UK)
    p_uk = {"id": 11, "company_number": "CF101", "name": "ACME UK Ltd", "award_count": 2}
    tier, reasons = compute_tier(p_uk, anchor_ltd, "ACME", ["f_uk_headoffice_stripped"])
    assert tier == 2
    assert "rule_f_uk_headoffice" in reasons

    # 3. Tier 2: bare single word with >= 10 awards
    p_bare = {"id": 12, "company_number": "CF102", "name": "ACME", "award_count": 15}
    tier, reasons = compute_tier(p_bare, anchor_ltd, "ACME", ["direct_match"])
    assert tier == 2
    assert any("bare_single_word" in r for r in reasons)

    # 4. Tier 2: legal form mismatch (PLC vs Ltd)
    p_plc = {"id": 13, "company_number": "CF103", "name": "ACME PLC", "award_count": 1}
    tier, reasons = compute_tier(p_plc, anchor_ltd, "ACME", ["direct_match"])
    assert tier == 2
    assert "legal_form_mismatch_PLC_vs_LTD" in reasons

