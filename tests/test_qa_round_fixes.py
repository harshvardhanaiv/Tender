"""Pure-function checks for the QA round fixes (archived notices, SME sanity)."""
import datetime
import unittest

from etenders_scraper.sources.etenders_portal import _is_archived


class ArchivedNoticeTests(unittest.TestCase):
    def test_old_notices_are_archived(self):
        self.assertTrue(_is_archived({"date_published": "12/03/2015", "submission_deadline": "30/04/2015"}))

    def test_recent_or_undated_notices_are_kept(self):
        year = datetime.date.today().year
        self.assertFalse(_is_archived({"date_published": f"01/02/{year}"}))
        self.assertFalse(_is_archived({"date_published": f"01/02/{year - 2}"}))
        self.assertFalse(_is_archived({}))

    def test_a_late_deadline_keeps_an_old_publication(self):
        year = datetime.date.today().year
        self.assertFalse(_is_archived({"date_published": "01/02/2015", "submission_deadline": f"01/02/{year}"}))


if __name__ == "__main__":
    unittest.main()
