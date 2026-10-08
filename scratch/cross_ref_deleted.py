"""Script to cross-reference the 15 reviewed supplier rows with the 49 deleted supplier rows."""
import sys

# The 15 supplier IDs & names from the step 8 review:
REVIEWED_15_BY_ID = {
    49444: ("Various for full details see OJEU award notice", 1),
    52672: ("Various as per OJEU Notice - Link contained in Attachments secti", 2),
    28474: ("Various Providers", 4),
    29938: ("Multiple providers under an AQP framework", 1),
    47554: ("New Forest National Park Authority - please see separate Contrac", 1),
    140390: ("Multiple Providers", 15),
    167584: ("All successful bidders as per original contract notice", 2),
    167585: ("All successful bidders as per original contract notice ref 2021-041133", 1),
    142455: ("Various Providers as per the F03 Notice", 2),
    145683: ("Multiple Providers on FPS, Please see below comment for list of providers", 1),
    103753: ("Various - please see description box for details", 1),
    104710: ("Various firms - please see additional details for listing", 1),
    152900: ("All as per original awards", 1),
    157199: ("Various Providers as per F03 Notice", 3),
    197390: ("Various - please refer to the Chest contract register", 1),
}

DELETED_49 = [
    (49444, "Various for full details see OJEU award notice", 1),
    (49488, "Various - Framework", 1),
    (52672, "Various as per OJEU Notice - Link contained in Attachments secti", 2),
    (725064, "Various - see details below", 1),
    (8888, "Various - see https:// www.lancashire.gov.uk/business/tenders-an", 3),
    (28474, "Various Providers", 4),
    (29938, "Multiple providers under an AQP framework", 1),
    (47554, "New Forest National Park Authority - please see separate Contrac", 1),
    (55663, "Various (See Additional Information)", 1),
    (56493, "Various - See Approved List - Publicly available via www.hants.g", 1),
    (57699, "Various (see additional details)", 1),
    (58514, "Various as indicated in Award Notice", 1),
    (61610, "Various Please see additional Information.", 1),
    (135061, "Various Pharmacies across Blackburn with Darwen", 1),
    (135064, "Various GP's across Blackburn with Darwen", 1),
    (136364, "Various Contractors (See Section VI: Complementary Information)", 1),
    (742069, "Various Contractors that were on Aster Livings preferred contrac", 1),
    (82809, "Various Care Homes in Redcar & Cleveland", 1),
    (140390, "Multiple Providers", 15),
    (164369, "Various South Gloucestershire Pharmacies c/o South Gloucestershire Council", 1),
    (164371, "Various South Gloucestershire GPs c/o South Gloucestershire Council", 1),
    (86412, "Various - see details", 1),
    (167584, "All successful bidders as per original contract notice", 2),
    (167585, "All successful bidders as per original contract notice ref 2021-041133", 1),
    (142455, "Various Providers as per the F03 Notice", 2),
    (747183, "Various SPV", 2),
    (145683, "Multiple Providers on FPS, Please see below comment for list of providers", 1),
    (103753, "Various - please see description box for details", 1),
    (104710, "Various firms - please see additional details for listing", 1),
    (109314, "Various Counsel Members", 2),
    (150318, "Various - as originally detailed", 1),
    (152900, "All as per original awards", 1),
    (157199, "Various Providers as per F03 Notice", 3),
    (157514, "Multiple providers (under the DPS)", 1),
    (157515, "Multiple providers on the DPS", 1),
    (179792, "Various Nottingham and Nottinghamshire GP Practices", 1),
    (188912, "Multiple Providers see weblink", 1),
    (760461, "Various GPs & Pharmacies", 1),
    (197390, "Various - please refer to the Chest contract register", 1),
    (201907, "Bolton General Practices - please refer to additional information", 1),
    (207190, "Various Companies", 2),
    (768095, "Various Contractors currently on Aster Livings preferred Contrac", 1),
    (709482, "Various - Framework Arrangement", 1),
    (717192, "Various Providers See Contract Award Notice", 1),
    (719158, "Various Income Generation", 1),
    (798505, "Various Local Authorities", 1),
    (808530, "Various Galleries", 1),
    (810437, "various individuals", 1),
    (30727, "Various - 1 - Wallace Sandwich Bar,2 - Jacksons Catering, 3 -Tre", 1),
]

in_reviewed = []
extra_34 = []

for sid, name, cnt in DELETED_49:
    if sid in REVIEWED_15_BY_ID:
        in_reviewed.append((sid, name, cnt))
    else:
        extra_34.append((sid, name, cnt))

print(f"Matched reviewed IDs: {len(in_reviewed)} / 15")
print(f"Extra deleted IDs: {len(extra_34)} / 34")

print("\n--- THE 15 REVIEWED SUPPLIERS (DELETED IN THIS APPLY) ---")
for sid, name, cnt in in_reviewed:
    print(f"  [ID {sid:6d}] ({cnt:2d} awards) '{name}'")

print("\n--- THE 34 EXTRA SUPPLIERS DELETED ---")
for idx, (sid, name, cnt) in enumerate(extra_34, 1):
    print(f"  {idx:02d}. [ID {sid:6d}] ({cnt:2d} awards) '{name}'")
