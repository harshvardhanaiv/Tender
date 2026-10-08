import requests
from bs4 import BeautifulSoup
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

urls = [
    "https://www.contractsfinder.service.gov.uk/Notice/bba66059-7694-4c2b-94f9-938bad3d589b",
    "https://www.contractsfinder.service.gov.uk/Notice/4b4d1c5e-083c-435e-a1b3-29fc77a4edb6",
    "https://www.contractsfinder.service.gov.uk/Notice/89dee72b-66f1-4415-9600-6a094711b9f4",
]

for url in urls:
    print("=" * 60)
    print("Fetching:", url)
    resp = requests.get(url, headers=headers, timeout=15)
    print("Status:", resp.status_code)
    soup = BeautifulSoup(resp.text, "lxml")
    title = soup.select_one("h1")
    print("Title:", title.get_text(strip=True) if title else "None")
    
    # Check notice fields
    for row in soup.select(".content-block, .govuk-summary-list__row, tr, p, .gadget"):
        txt = row.get_text(" ", strip=True)
        if any(k in txt.lower() for k in ["value", "framework", "award", "contract value", "total value", "lot", "description"]):
            if len(txt) > 20 and len(txt) < 300:
                print("  ->", txt)
