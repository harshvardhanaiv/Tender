import urllib.request
import hashlib
from pathlib import Path

local_path = Path("web/styles.css")
with open(local_path, "rb") as f:
    local_data = f.read()
local_hash = hashlib.sha256(local_data).hexdigest()

req = urllib.request.Request("https://tender.civenta.co.uk/styles.css", headers={"User-Agent": "Mozilla/5.0"})
live_data = urllib.request.urlopen(req).read()
live_hash = hashlib.sha256(live_data).hexdigest()

print("Local styles.css size:", len(local_data), "hash:", local_hash[:16])
print("Live styles.css size: ", len(live_data), "hash:", live_hash[:16])
print("Match?", local_hash == live_hash)
