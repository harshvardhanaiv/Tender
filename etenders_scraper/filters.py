from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Logical CLI names -> substrings matched against form field `name` attributes.
FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "resource_id": ("resourceid", "resource.id", "eppsid"),
    "title": ("title",),
    "ca_unique_id": ("caunique", "ca.unique", "cauniqueid"),
    "contracting_authority": ("contractingauthority", "authorityname", "caname"),
    "description": ("description", "desc"),
    "status": ("status", "workspace"),
    "procurement_type": ("procurementtype", "contracttype", "contract.type"),
    "procedure": ("procedure",),
    "submission_from": ("submissionfrom", "tendersubmissionfrom", "deadlinefrom"),
    "submission_to": ("submissionto", "tendersubmissionto", "deadlineto"),
    "opening_from": ("openingfrom", "tenderopeningfrom"),
    "opening_to": ("openingto", "tenderopeningto"),
    "publication_from": ("publicationfrom", "publishedfrom"),
    "publication_to": ("publicationto", "publishedto"),
    "value_min": ("valuemin", "estimatedmin", "amountmin"),
    "value_max": ("valuemax", "estimatedmax", "amountmax"),
    "threshold": ("threshold",),
    "cpv": ("cpv",),
    "nuts": ("nuts",),
}


@dataclass
class SearchFilters:
    resource_id: str | None = None
    title: str | None = None
    ca_unique_id: str | None = None
    contracting_authority: str | None = None
    description: str | None = None
    status: str | None = None
    procurement_type: str | None = None
    procedure: str | None = None
    submission_from: str | None = None
    submission_to: str | None = None
    opening_from: str | None = None
    opening_to: str | None = None
    publication_from: str | None = None
    publication_to: str | None = None
    value_min: str | None = None
    value_max: str | None = None
    threshold: str | None = None
    cpv: str | None = None
    nuts: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, str | None]:
        data = {
            key: getattr(self, key)
            for key in FIELD_HINTS
        }
        data.update(self.extra)
        return {key: value for key, value in data.items() if value}


def apply_filters(form_fields: dict[str, str], filters: SearchFilters) -> dict[str, str]:
    """Merge user filters into discovered form defaults."""
    payload = dict(form_fields)
    requested = filters.as_dict()

    for logical_name, value in requested.items():
        if logical_name in FIELD_HINTS:
            target = _match_field_name(payload, FIELD_HINTS[logical_name])
            if target:
                payload[target] = value
            elif logical_name == "description":
                # Last resort: set every description-like field.
                for name in payload:
                    if "description" in name.lower():
                        payload[name] = value
            continue
        payload[logical_name] = value

    return payload


def _match_field_name(form_fields: dict[str, str], hints: tuple[str, ...]) -> str | None:
    for name in form_fields:
        normalized = name.lower().replace("_", "").replace("-", "").replace(".", "")
        if any(hint in normalized for hint in hints):
            return name
    return None


def discover_field_map(form_fields: dict[str, str]) -> dict[str, str | None]:
    """Map logical filter names to actual HTML field names (for --discover-fields)."""
    mapping: dict[str, str | None] = {}
    for logical, hints in FIELD_HINTS.items():
        mapping[logical] = _match_field_name(form_fields, hints)
    return mapping
