"""Check if any of the 15 reviewed names were in suppliers table or contract_awards table before this apply."""
import sys
import os

REVIEWED_15_NAMES = [
    "As per Supplier Table",
    "As per address",
    "As per attachment",
    "Awarded supplier details as per attached list",
    "Confidential Information",
    "Confidential / Sensitive Information",
    "Information withheld as confidential",
    "Please See additional information",
    "Please see Contract Award Notice",
    "Please see contract notice",
    "Please see notice",
    "Please refer to contract notice",
    "Please refer to notice",
    "Various suppliers",
    "Various firms"
]

# Let's check what suppliers exist or existed in data dump or contract awards for these 15 names
# We can check data/planning_sync.sql or scratch logs
print("Checking the 15 reviewed names:")
for r in REVIEWED_15_NAMES:
    print(" -", r)
