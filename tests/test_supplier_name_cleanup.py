import unittest
import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.supplier_name_cleanup import classify_supplier, clean_lot_score_name, is_award_junk_supplier_name


class TestSupplierNameCleanupClassification(unittest.TestCase):
    def test_award_junk_supplier_name_nulling(self):
        """Proves that placeholders, attachment phrases, and prose descriptions are flagged for nulling on contract_awards,
        while Category B names are preserved."""
        junk_award_names = [
            "See attachment",
            "Please see attachment",
            "Live Scraped Supplier",
            "See attachment for breakdown",
            "Please refer to attachment section for list of successfully awarded suppliers",
            "The existing provider is satisfying the original contract and will likely satisfy the proposed contract to a sufficient standard",
        ]
        for name in junk_award_names:
            self.assertTrue(is_award_junk_supplier_name(name), f"Expected '{name}' to be flagged as award junk")

        protected_b_names = [
            "Will Build Contracting Limited",
            "To the Moon and Back Foster Care Ltd",
            "Purple Surgical (prev. Cory Bros)",
            "HSO - Herts Schools Outreach (UK) CIC (HSO - Herts Schools Outreach (UK) CIC and NESHertfordshireSie)",
        ]
        for name in protected_b_names:
            self.assertFalse(is_award_junk_supplier_name(name), f"Expected '{name}' to NOT be flagged as award junk")
    def test_lot_score_name_cleaning(self):
        """Proves that lot score suffixes like R2: 2R 3R 4C... are stripped and the company name is preserved."""
        raw_name = "Balfour Beatty Civil Engineering Limited R2:  2R 3R 4C 6C 7R 9C, R3:  2R 3R 4C 6C 7R 9C, R4: 1R 2C 3C 4C 6C 7C 9C, R5: 1C 2C 3C 4C 6C 7C 9C, R6: 1R 2R 3C 4C 6C 7C 9C"
        cleaned = clean_lot_score_name(raw_name)
        self.assertEqual(cleaned, "Balfour Beatty Civil Engineering Limited")

        raw_rhodar = "Rhodar Industrial Services Limited : R1a: 2c 3c 4c, R1b: 2c 3c 4c, R1c: 2c 3c 4c"
        self.assertEqual(clean_lot_score_name(raw_rhodar), "Rhodar Industrial Services Limited")

    def test_category_b_example_names_never_deleted(self):
        """Proves that real-looking single companies with long names, (t/a ...) or (part of ...) suffixes
        are categorized as 'B' and therefore protected from deletion."""
        example_b_names = [
            'FRAUNHOFER-GESELLSCHAFT ZUR FORDERUNG DER ANGEWANDTEN FORSCHUNG E.V T/A Fraunhofer Institute for Integrated Circuits (Fraunhofer IIS)',
            'KWIK-FIT (GB) LIMITED (part of the European Tyre Enterprise Limited group which also includes Credential Environmental Limited)',
            'MA Cost Consulting Limited (t/a MAC Construction Consultants)MA Cost Consulting Limited (t/a MAC Construction Consultants)',
            'University Hospital Coventry (UHCW) and Warwickshire NHS Trust hosting  Coventry and Warwickshire Pathology Service (CWPS)',
            'Purple Surgical (prev. Cory Bros)',
            'Will Build Contracting Limited',
            'To the Moon and Back Foster Care Ltd',
            'HSO - Herts Schools Outreach (UK) CIC (HSO - Herts Schools Outreach (UK) CIC and NESHertfordshireSie)',
        ]

        for name in example_b_names:
            category = classify_supplier(name)
            self.assertEqual(category, "B", f"Supplier name '{name}' should be categorized as 'B' (protected). Got '{category}' instead.")

    def test_category_a_junk_names(self):
        """Proves that clear prose, URLs, and attachment instructions are categorized as 'A' (junk)."""
        junk_names = [
            'See attachment for breakdown',
            'Please refer to attachment section for list of successfully awarded suppliers',
            'https://www.oxfordshire.gov.uk/council/about-your-council/council-tax-and-finance',
            'The existing provider is satisfying the original contract and will likely satisfy the proposed contract to a sufficient standard',
        ]

        for name in junk_names:
            category = classify_supplier(name)
            self.assertEqual(category, "A", f"Junk name '{name}' should be categorized as 'A'. Got '{category}' instead.")

    def test_category_c_company_lists(self):
        """Proves that comma/colon separated lists of multiple companies are categorized as 'C'."""
        list_names = [
            'HW Martin, Amberon Ltd, Hooke Highwyas, Chevron Traffic Management, Sunbelt Rentals, Idverde Ltd',
            'Fox Building & Engineering Ltd, Adman Civil Projects Ltd, William & Henry Alexander (Civil Engineering) Limited, Lowry Building & Civil Engineering Ltd',
            'Capsticks Solicitors LLP - London, Foot Anstey LLP - Plymouth, Penningtons Manches Cooper LLP - London',
        ]

        for name in list_names:
            category = classify_supplier(name)
            self.assertEqual(category, "C", f"Company list '{name}' should be categorized as 'C'. Got '{category}' instead.")

    def test_id_rules_require_matching_name(self):
        """An id from the override sets with a DIFFERENT name must not be deleted: rule skipped, WARNING printed."""
        import io
        import contextlib
        from scripts.supplier_name_cleanup import FORCED_JUNK_IDS, MULTI_CO_IDS, FORCED_KEEP_B_IDS

        # Matching id + name -> rule applies
        self.assertEqual(classify_supplier(756697, "To Be Confirmed"), "A")
        self.assertEqual(classify_supplier(756697, "  to be  confirmed "), "A")
        self.assertEqual(classify_supplier(189383, MULTI_CO_IDS[189383]), "C")
        # Truncated expected name matches as a leading fragment; en-dash normalised
        self.assertEqual(classify_supplier(152677, "HSO – Herts Schools Outreach (UK) CIC (HSO – Herts Schools Outreach (UK) CIC and NESHertfordhsireSie IN ED CIC – Consortium)"), "B")

        # Same ids, different (real-looking) supplier names -> NOT forced to A/C
        for rules, sid, label in [(FORCED_JUNK_IDS, 756697, "FORCED_JUNK"),
                                  (MULTI_CO_IDS, 150909, "MULTI_CO"),
                                  (FORCED_JUNK_IDS, 229940, "FORCED_JUNK")]:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                category = classify_supplier(sid, "Acme Widgets Limited")
            self.assertEqual(category, "B", f"id {sid} with a different name must not be deleted")
            self.assertIn("WARNING", err.getvalue())
            self.assertIn(str(sid), err.getvalue())
            self.assertIn("Acme Widgets Limited", err.getvalue())
            self.assertIn(label, err.getvalue())

        # A protected id with a different name gets no ID protection (falls back to name rules)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            category = classify_supplier(152677, "The existing provider is satisfying the original contract and will likely satisfy the proposed contract")
        self.assertEqual(category, "A")
        self.assertIn("WARNING", err.getvalue())

    def test_live_flag_missing_env_vars_exits_with_error(self):
        """Proves that running with --live when LIVE_DB_* env vars are missing exits with an error code."""
        import subprocess
        env = os.environ.copy()
        for k in ["LIVE_DB_HOST", "LIVE_DB_PORT", "LIVE_DB_NAME", "LIVE_DB_USER", "LIVE_DB_PASSWORD"]:
            env.pop(k, None)

        cmd = [sys.executable, "scripts/supplier_name_cleanup.py", "--live"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Expected non-zero exit code when LIVE_DB_* env vars are missing")
        self.assertIn("LIVE_DB_", res.stderr)

    def test_live_apply_without_confirm_live_exits_with_error(self):
        """Proves that running --apply with --live without --confirm-live exits with an error code."""
        import subprocess
        env = os.environ.copy()
        env["LIVE_DB_HOST"] = "127.0.0.1"
        env["LIVE_DB_PORT"] = "15440"
        env["LIVE_DB_NAME"] = "postgres"
        env["LIVE_DB_USER"] = "postgres"
        env["LIVE_DB_PASSWORD"] = "dummy"

        cmd = [sys.executable, "scripts/supplier_name_cleanup.py", "--live", "--apply"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Expected non-zero exit code when --apply is passed without --confirm-live")
        self.assertIn("--confirm-live", res.stderr)


if __name__ == "__main__":
    unittest.main()
