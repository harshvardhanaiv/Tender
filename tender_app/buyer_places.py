"""Where a buyer is, from the place names in its own name (Re-test 4 B6).

8,035 buyers have no stored location, so "25 km around RG2 7TB" found none of them. Most of those names carry a place: "Royal Berkshire
NHS Foundation Trust", "University of Reading", "Reading Borough Council". The places are the planning authorities (about 400 areas
whose centre is the middle of their planning applications), so the position is the area's centre: approximate, and labelled so.

  place_index(cur)              place words -> (lat, lon, area name), unambiguous planning authorities only
  locate_by_place(name, index)  the position of the longest place named in `name`, or None
"""
from __future__ import annotations

import re
from typing import Any

# Words that decorate a body's name but are not a place, and place words too common to place a buyer ("North", "New").
NOISE = {"council", "borough", "city", "district", "county", "london", "metropolitan", "royal", "of", "the", "and", "unitary",
         "authority", "corporation", "combined", "shire", "&"}
TOO_COMMON = {"north", "south", "east", "west", "central", "new", "great", "upper", "lower", "greater", "city", "town", "park", "hall"}
MIN_APPLICATIONS = 5   # a centre from a handful of applications is not trustworthy


def place_key(name: str | None) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in NOISE)


def place_index(cur) -> dict[str, tuple[float, float, str]]:
    """key -> (lat, lon, area name) for planning authorities whose key points at one place only."""
    cur.execute("""
        SELECT a.area_name, a.long_name,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY p.latitude),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY p.longitude)
        FROM planning_applications p JOIN planning_areas a ON a.area_id = p.authority_id
        WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL
        GROUP BY a.area_id, a.area_name, a.long_name
        HAVING count(*) >= %s
    """, (MIN_APPLICATIONS,))
    by_key: dict[str, list[tuple[float, float, str]]] = {}
    for area, long_name, lat, lon in cur.fetchall():
        for k in {place_key(area), place_key(long_name)}:
            if k and k not in TOO_COMMON and len(k) >= 4:
                by_key.setdefault(k, []).append((float(lat), float(lon), area))
    return {k: v[0] for k, v in by_key.items() if len({(round(x[0], 3), round(x[1], 3)) for x in v}) == 1}


def locate_by_place(name: str | None, index: dict[str, Any]) -> tuple[float, float, str] | None:
    """The longest run of the name's words (up to four) that is a place in `index`; None when the name holds no place."""
    words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
    if not words or not index:
        return None
    for size in (4, 3, 2, 1):
        for i in range(len(words) - size + 1):
            hit = index.get(" ".join(words[i:i + size]))
            if hit:
                return hit
    return None
