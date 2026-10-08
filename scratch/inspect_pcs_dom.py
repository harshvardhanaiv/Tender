import requests
from bs4 import BeautifulSoup
import re

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
soup = BeautifulSoup(post_resp.text, "lxml")

print("--- Inspecting results container ---")
# Find links to search_view
links = soup.find_all("a", href=re.compile(r"search_view\.aspx\?ID=", re.I))
print(f"Total links to search_view.aspx?ID=: {len(links)}")
for i, l in enumerate(links[:10]):
    print(f"Link {i+1}: text={l.get_text(strip=True)[:50]} | href={l['href']}")
    p = l.parent
    print(f"  Parent tag: <{p.name} class='{p.get('class')}'>")
    gp = p.parent if p else None
    print(f"  Grandparent tag: <{gp.name} class='{gp.get('class')}'>")

# Check what classes exist on items
tables = soup.find_all("table")
print(f"Total tables: {len(tables)}")
for t in tables:
    if t.get("class") or t.get("id"):
        print(f"Table id={t.get('id')}, class={t.get('class')}")

# Check any div containing search results
for div in soup.find_all("div", class_=True):
    cls = " ".join(div.get("class"))
    if any(k in cls.lower() for k in ["result", "notice", "item", "record", "list"]):
        print(f"Div class: {cls}")
