"""Run: python tests/test_county_filter_portals.py   (plain asserts, no test framework needed).

The county filter hides every row whose portal it does not recognise, so the lists of UK/Ireland
portals have to agree: UK_IE_SOURCE_IDS in the registry (used by server.py), its copy in
web/uk_counties.js, and the portals named in REGION_PORTAL_IDS in web/app.js. ProContract and GCA
both vanished from England-only searches (Round 26) because they were added to the search but not
to the filter.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from etenders_scraper.sources.registry import SOURCES, UK_IE_SOURCE_IDS  # noqa: E402


def _quoted_ids(block: str) -> set[str]:
    # portal ids are lower snake_case; region names ("England") start with a capital
    return set(re.findall(r'"([a-z][a-z0-9_]*)"', block))


def _client_filter_ids() -> set[str]:
    text = (ROOT / "web" / "uk_counties.js").read_text(encoding="utf-8")
    m = re.search(r"const UK_IE_SOURCE_IDS = new Set\(\[(.*?)\]\);", text, re.S)
    assert m, "web/uk_counties.js no longer defines UK_IE_SOURCE_IDS"
    return _quoted_ids(m.group(1))


def _region_portal_ids() -> dict[str, set[str]]:
    text = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const REGION_PORTAL_IDS = \{(.*?)\};", text, re.S)
    assert m, "web/app.js no longer defines REGION_PORTAL_IDS"
    out = {}
    for region, ids in re.findall(r'"([A-Z][A-Za-z ]+)":\s*\[(.*?)\]', m.group(1), re.S):
        out[region] = _quoted_ids(ids)
    assert out, "could not read any region from REGION_PORTAL_IDS"
    return out


def main() -> None:
    unknown = set(UK_IE_SOURCE_IDS) - set(SOURCES)
    assert not unknown, f"UK_IE_SOURCE_IDS names portals missing from SOURCES: {sorted(unknown)}"

    client = _client_filter_ids()
    assert client == set(UK_IE_SOURCE_IDS), (
        "web/uk_counties.js and registry.UK_IE_SOURCE_IDS disagree: "
        f"only in JS {sorted(client - UK_IE_SOURCE_IDS)}, only in registry {sorted(UK_IE_SOURCE_IDS - client)}"
    )

    for region, ids in _region_portal_ids().items():
        missing = ids - set(UK_IE_SOURCE_IDS)
        assert not missing, (
            f"REGION_PORTAL_IDS['{region}'] has portals the county filter would hide completely: {sorted(missing)}"
        )

    # the two portals Round 26 lost
    for sid in ("procontract", "gca_agreements"):
        assert sid in UK_IE_SOURCE_IDS, f"{sid} must be known to the county filter"

    print(f"OK: {len(UK_IE_SOURCE_IDS)} UK/IE portals agree across registry, uk_counties.js and REGION_PORTAL_IDS")


if __name__ == "__main__":
    main()
