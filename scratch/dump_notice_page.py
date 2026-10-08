import requests
from bs4 import BeautifulSoup

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

url = "https://www.contractsfinder.service.gov.uk/Notice/bba66059-7694-4c2b-94f9-938bad3d589b"
resp = requests.get(url, headers=headers, timeout=15)
soup = BeautifulSoup(resp.text, "lxml")

main = soup.select_one("main") or soup.select_one("#content") or soup
for block in main.find_all(["div", "section"]):
    if block.get("class") and any("content" in c or "summary" in c or "detail" in c for c in block.get("class")):
        txt = block.get_text("\n", strip=True)
        if len(txt) > 50:
            print(txt)
            print("="*60)
