import requests
from bs4 import BeautifulSoup
import re

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

search_url = "https://www.publiccontractsscotland.gov.uk/Search/Search_MainPage.aspx"

print("--- 1. Testing GET on Search_MainPage.aspx ---")
resp_get = session.get(search_url, timeout=30)
print(f"Status: {resp_get.status_code}, length: {len(resp_get.text)}")
soup_get = BeautifulSoup(resp_get.text, "lxml")
btn_search = soup_get.find("input", {"name": "ctl00$maincontent$btnSearch"})
print(f"Has btnSearch on GET form: {btn_search is not None}")

form = soup_get.find("form", id="aspnetForm")
fields = {}
for inp in form.find_all("input"):
    name = inp.get("name")
    if name and (inp.get("type") or "text").lower() not in ("submit", "button", "image"):
        fields[name] = inp.get("value", "")
for sel in form.find_all("select"):
    name = sel.get("name")
    if name:
        opt = sel.find("option", selected=True) or sel.find("option")
        fields[name] = opt.get("value", "") if opt else ""

print(f"Extracted {len(fields)} fields from form")

print("\n--- 2. Performing proper POST search for 'construction' ---")
data = dict(fields)
data["ctl00$maincontent$txtKeywords"] = "construction"
data["ctl00$maincontent$btnSearch"] = "Search"

post_resp = session.post(search_url, data=data, timeout=35)
print(f"POST status: {post_resp.status_code}, length: {len(post_resp.text)}")
soup_post = BeautifulSoup(post_resp.text, "lxml")
cards = soup_post.select(".search-result")
print(f"Number of .search-result cards found: {len(cards)}")

# Check pagination & total results text
txt = soup_post.get_text()
for match in re.finditer(r"(\d[\d,]*)\s+(?:results?|notices?|records?)|Page\s+\d+\s+of\s+(\d+)", txt, re.I):
    print("Match:", match.group(0))

select_pager = soup_post.select_one("select[id*='ddPageSelect']")
if select_pager:
    options = select_pager.find_all("option")
    print(f"ddPageSelect options count: {len(options)}, last value: {options[-1].get_text() if options else 'none'}")

header_info = soup_post.select(".results-count, .search-results-summary, .search-title, h1, h2, h3")
for h in header_info:
    if "result" in h.get_text().lower() or "notice" in h.get_text().lower() or "search" in h.get_text().lower():
        print(f"Header: {h.get_text(strip=True)}")

if cards:
    print("\nSample first card:")
    title_el = cards[0].select_one("a.notice-title, a")
    print("  Title:", title_el.get_text(strip=True) if title_el else "None")
    print("  URL:", title_el.get("href") if title_el else "None")
