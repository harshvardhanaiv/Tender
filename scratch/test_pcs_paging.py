import os
import sys
import re
import requests
from bs4 import BeautifulSoup
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
})

search_url = "https://www.publiccontractsscotland.gov.uk/Search/Search_MainPage.aspx"
resp_get = session.get(search_url, timeout=30)
soup_get = BeautifulSoup(resp_get.text, "lxml")
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

fields["ctl00$maincontent$txtKeywords"] = "construction"
fields["ctl00$maincontent$btnSearch"] = "Search"

post_resp = session.post(search_url, data=fields, timeout=35)
print("Page 1 length:", len(post_resp.text))

# Let's inspect the pager element on post_resp
soup_p1 = BeautifulSoup(post_resp.text, "lxml")
select = soup_p1.select_one("select[id*='ddPageSelect']")
print("Select tag id:", select.get("id") if select else None, "name:", select.get("name") if select else None)

# Test POST for page 2 using _essential_asp_fields
from etenders_scraper.sources.bravo_search import _essential_asp_fields, _PCS_PAGE_TARGET, _PCS_PAGE_SELECT, _parse_results_html

base_fields = _essential_asp_fields(post_resp.text)
data2 = {
    **base_fields,
    "__EVENTTARGET": _PCS_PAGE_TARGET,
    "__EVENTARGUMENT": "",
    _PCS_PAGE_SELECT: "2",
}
print("Posting for Page 2 with target:", _PCS_PAGE_TARGET, "select name:", _PCS_PAGE_SELECT)
resp_p2 = session.post(search_url, data=data2, timeout=35)
print("Page 2 status:", resp_p2.status_code, "length:", len(resp_p2.text))
match_p2 = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", resp_p2.text, re.I)
print("Page 2 indicator in text:", match_p2.group(0) if match_p2 else "Not found")

# Check rows parsed
rows_p1 = _parse_results_html(post_resp.text, "https://www.publiccontractsscotland.gov.uk")
rows_p2 = _parse_results_html(resp_p2.text, "https://www.publiccontractsscotland.gov.uk")
print(f"Parsed rows p1: {len(rows_p1)}, p2: {len(rows_p2)}")
for r in rows_p2[:5]:
    print("  P2 row:", r.get("title"))
