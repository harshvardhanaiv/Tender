"""A user's global watchlist of buyers and suppliers, kept next to the tender Pipeline.

An entry is (kind, entity_key): kind is "buyer" or "supplier"; entity_key is the buyer_key / supplier id the page
already uses. `added_from` records the section the user added it in, so the Pipeline can show where every lead came from.
Pure helpers here; the HTTP layer is tender_app/blueprints/watchlist_bp.py.
"""
from __future__ import annotations

from typing import Any

KINDS = ("buyer", "supplier")
SOURCES = {
    "supplier_intelligence": "Supplier Intelligence",
    "buyer_intelligence": "Buyer Intelligence",
    "growth_studio": "Growth Studio",
    "open_opportunity": "Open opportunity",
    "tender_search": "Tender search",
    "market_radar": "Market Radar",
    "map": "Map",
}
MAX_ENTRIES = 500


def clean_entry(body: Any) -> dict[str, str] | None:
    """The fields of a valid add request, or None. Unknown sources are stored as 'other' rather than rejected."""
    if not isinstance(body, dict):
        return None
    kind = str(body.get("kind") or "").strip().lower()
    key = str(body.get("entity_key") if body.get("entity_key") is not None else "").strip()[:200]
    name = " ".join(str(body.get("name") or "").split())[:300]
    if kind not in KINDS or not key or not name:
        return None
    source = str(body.get("added_from") or "").strip().lower()
    return {"kind": kind, "entity_key": key, "name": name,
            "added_from": source if source in SOURCES else "other",
            "note": str(body.get("note") or "").strip()[:1000]}


def public(row: tuple) -> dict[str, Any]:
    kind, key, name, source, note, created = row
    return {"kind": kind, "entity_key": key, "name": name, "added_from": source,
            "added_from_label": SOURCES.get(source, "Other"), "note": note or "", "created_at": str(created)}
