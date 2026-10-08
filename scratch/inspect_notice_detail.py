import requests
from bs4 import BeautifulSoup
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

url = "https://www.contractsfinder.service.gov.uk/Notice/bba66059-7694-4c2b-94f9-938bad3d589b"
resp = requests.get(url, headers=headers, timeout=15)
soup = BeautifulSoup(resp.text, "lxml")

print("--- Details of Notice bba66059-7694-4c2b-94f9-938bad3d589b ---")
for el in soup.find_all(["h2", "h3", "p", "div", "li"]):
    txt = el.get_text(" ", strip=True)
    if any(k in txt.lower() for k in ["total value", "contract value", "mss/143", "framework", "call-off", "task", "order", "pd2-003", "ceiling"]):
        if len(txt) < 250:
            print("  ", txt)
