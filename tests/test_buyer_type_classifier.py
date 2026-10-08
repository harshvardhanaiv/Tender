"""One buyer-type classifier, used at ingestion and by the reclassify script.

Live check after Round 28: Riverside Group was typed "Other public bodies" (a housing provider) and the British Council "Other local government"
(its name contains "council"). Also the ingestion copy in etenders_scraper/awards.py still had the bare "association" rule that the buyers_bp copy had
dropped, so the next sync would have put the Local Government Association back under Housing. There is now one implementation.
Run: python tests/test_buyer_type_classifier.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5440")
os.environ.setdefault("DB_NAME", "postgres")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", os.environ.get("LOCAL_DB_PASSWORD", "postgres"))

from etenders_scraper import awards
from tender_app.blueprints import buyers_bp


def test_ingestion_and_reclassify_share_one_classifier():
    assert buyers_bp.classify_buyer_type is awards.classify_buyer_type
    print("ok    test_ingestion_and_reclassify_share_one_classifier")


def test_names_that_look_like_councils_or_trusts_but_are_not():
    c = awards.classify_buyer_type
    assert c("The Riverside Group Ltd") == "Housing Associations"
    assert c("Peabody Trust") == "Housing Associations"
    assert c("Metropolitan Thames Valley Housing") == "Housing Associations"   # "metropolitan" must not make it a council
    assert c("Notting Hill Genesis") == "Housing Associations"
    assert c("British Council") == "Other Public Bodies"
    assert c("The Arts Council England") == "Other Public Bodies"
    assert c("The Arts Council of England") == "Other Public Bodies"
    assert c("Arts Council Of Wales") == "Other Public Bodies"
    print("ok    test_names_that_look_like_councils_or_trusts_but_are_not")


def test_bare_association_is_not_housing():
    c = awards.classify_buyer_type
    assert c("Local Government Association") == "Other Public Bodies"
    assert c("Workers' Educational Association") == "Other Public Bodies"
    assert c("Hyde Housing Association Ltd") == "Housing Associations"
    assert c("Places for People Group") == "Housing Associations"
    print("ok    test_bare_association_is_not_housing")


def test_real_councils_and_the_other_types_are_unchanged():
    c = awards.classify_buyer_type
    assert c("London Borough of Camden Council") == "Local Government / Council"
    assert c("Metropolitan Borough of Wigan") == "Local Government / Council"
    assert c("Leeds City Council") == "Local Government / Council"
    assert c("NHS England") == "NHS & Healthcare"
    assert c("Ministry of Defence") == "Central Government & Agencies"
    assert c("Greater Manchester Police") == "Police & Emergency Services"
    assert c("University of Southampton") == "Education & Academies"
    assert c("") == "Other Public Bodies"
    print("ok    test_real_councils_and_the_other_types_are_unchanged")


if __name__ == "__main__":
    test_ingestion_and_reclassify_share_one_classifier()
    test_names_that_look_like_councils_or_trusts_but_are_not()
    test_bare_association_is_not_housing()
    test_real_councils_and_the_other_types_are_unchanged()
    print("4 passed, 0 skipped, 0 failed")
