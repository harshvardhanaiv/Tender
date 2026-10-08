"""Known CfT workspace field labels (prepareViewCfTWS.do) in display order."""

from __future__ import annotations

import re
from typing import Any

# Longest labels first so regex splitting matches correctly.
CFT_WORKSPACE_LABELS: tuple[str, ...] = (
    "Allow different Evaluation Mechanism in the MC/SC",
    "Allow suppliers to make an online Expression Of Interest",
    "Time-limit for receipt of tenders or requests to participate",
    "Contract duration in months or years, including any options and renewals",
    "Name of Contracting Authority",
    "End of clarification period",
    "Deadline for dispatching invitations",
    "Date of Publication/Invitation",
    "TED links for published notices",
    "Validity of Tender in days or months",
    "Multiple tenders will be accepted",
    "Estimated value (EUR)",
    "Above or Below threshold",
    "Evaluation Mechanism",
    "Inclusion of e-Auctions",
    "CfT CA Unique ID",
    "Language of publication",
    "Awarded (CAN) value",
    "Contract Award Date",
    "Date of Awarding",
    "Tenders Opening Date",
    "Procurement Type",
    "Contact Point",
    "Contact Name",
    "Contact Email",
    "Contact Phone",
    "Number of openers",
    "Contract awarded in Lots",
    "Award per Item",
    "CfT Involves",
    "CPC Category",
    "CPV Codes",
    "NUTS codes",
    "Description",
    "Procedure",
    "Directive",
    "Title",
    "EU funding",
)

# Preferred column order for CSV/Excel export.
EXPORT_COLUMN_ORDER: tuple[str, ...] = (
    "resource_id",
    "title",
    "name_of_contracting_authority",
    "description",
    "estimated_value_eur",
    "cpv_codes",
    "procedure",
    "procurement_type",
    "cpc_category",
    "directive",
    "cft_involves",
    "nuts_codes",
    "contract_duration_in_months_or_years_including_any_options_and_renewals",
    "contract_duration",
    "above_or_below_threshold",
    "evaluation_mechanism",
    "submission_deadline",
    "tenders_opening_date",
    "end_of_clarification_period",
    "date_published",
    "status",
    "award_date",
    "contract_award_date",
    "awarded_can_value",
    "contact_point",
    "contact_name",
    "contact_email",
    "contact_phone",
    "validity_of_tender",
    "eu_funding",
    "ted_links",
    "detail_url",
    "notice_pdf_url",
)


def label_to_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")


def parse_cft_workspace_text(text: str) -> dict[str, str]:
    """Parse 'Label: Value Label: Value' blocks from CfT workspace page text."""
    if not text:
        return {}

    # Strip boilerplate before first known label when possible.
    start = None
    for label in CFT_WORKSPACE_LABELS:
        idx = text.find(label + ":")
        if idx != -1 and (start is None or idx < start):
            start = idx
    if start is not None:
        text = text[start:]

    labels_sorted = sorted(CFT_WORKSPACE_LABELS, key=len, reverse=True)
    pattern = "|".join(re.escape(label) for label in labels_sorted)
    parts = re.split(rf"({pattern}):", text)
    if len(parts) < 3:
        return {}

    result: dict[str, str] = {}
    index = 1
    while index + 1 < len(parts):
        label = parts[index].strip()
        value = parts[index + 1].strip()
        key = label_to_key(label)
        if key and value:
            result[key] = value
        index += 2
    return result


def order_export_columns(columns: list[str]) -> list[str]:
    ordered = [col for col in EXPORT_COLUMN_ORDER if col in columns]
    remaining = sorted(col for col in columns if col not in ordered)
    return ordered + remaining


_FIELD_ALIASES: dict[str, str] = {
    "contracting_authority": "name_of_contracting_authority",
    "time_limit_for_receipt_of_tenders_or_requests_to_participate": "submission_deadline",
    "date_of_publication_invitation": "date_published",
}


def _apply_aliases(row: dict[str, Any]) -> dict[str, Any]:
    for source, target in _FIELD_ALIASES.items():
        if row.get(source) and not row.get(target):
            row[target] = row[source]
    return row


def flatten_for_export(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize rows to consistent keys and column order."""
    if not rows:
        return []
    normalized = [_apply_aliases(dict(row)) for row in rows]
    all_keys: set[str] = set()
    for row in normalized:
        all_keys.update(row.keys())
    column_order = order_export_columns(list(all_keys))
    return [{col: row.get(col, "") for col in column_order} for row in normalized]
