import urllib.parse
import hashlib
import re
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

def search_meta_portal(keyword: str, source_id: str, source_label: str, domain: str) -> list[dict]:
    results = []
    seen_links = set()
    query = f'site:{domain} "{keyword}"' if keyword and keyword.strip() else f'site:{domain} procurement tender'
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    # Fast single page web search
    url = f"https://search.yahoo.com/search?p={urllib.parse.quote_plus(query)}&b=1"
    try:
        resp = session.get(url, timeout=(1.5, 2.0))
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for item in soup.find_all("div", class_="algo"):
                h3 = item.find("h3")
                a = h3.find("a") if h3 else item.find("a")
                snippet_div = item.find("div", class_="compText") or item.find("span", class_="fc-t")
                if a:
                    title = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
                    link = a.get("href", "")
                    if "/RU=" in link:
                        parts = link.split("/RU=")
                        if len(parts) > 1:
                            target = parts[1].split("/")[0]
                            link = urllib.parse.unquote(target)
                    
                    if not link or link in seen_links:
                        continue
                    seen_links.add(link)
                    
                    title = re.sub(r"^https?://\S+", "", title).strip()
                    title = re.sub(r"^[A-Za-z0-9\.\-]+https?://\S*", "", title).strip()
                    title = re.sub(r"^[A-Za-z0-9\.\-]+\s*›\s*", "", title).strip()
                    title = re.sub(r"\s+", " ", title).strip()
                    if not title or len(title) < 4:
                        title = f"{source_label} Opportunity"

                    desc = snippet_div.get_text(strip=True) if snippet_div else f"Procurement notice published by {source_label}."
                    res_id = hashlib.md5(link.encode("utf-8")).hexdigest()[:12].upper()
                    
                    results.append({
                        "resource_id": res_id,
                        "title": title,
                        "description": desc,
                        "contracting_authority": f"Public Authority ({source_label})",
                        "estimated_value_eur": "N/A",
                        "cpv_codes": "N/A",
                        "procedure": "Open Opportunity",
                        "procurement_type": "Services",
                        "submission_deadline": "See official notice",
                        "date_published": datetime.now().strftime("%Y-%m-%d"),
                        "detail_url": link,
                        "source": source_id,
                        "source_label": source_label,
                    })
    except Exception as e:
        print(f"Meta search Yahoo error for {source_label}: {e}")

    return results
