"""Lead score: a transparent 0-100 ranking of how promising a planning application is
as a pre-tender opportunity.

This is a documented heuristic, not AI and not a value estimate. Every point is
explainable from fields on the record, and `explain_lead_score` returns the breakdown so
the UI can show users exactly why a scheme ranks where it does.

Weights, and why (scaled by 100/115 from an earlier 40/25/15/20/15 set so the five
components can never sum past 100 -- see the "cap: 100" note below for why that mattered):
  size      Large 35, Medium 17       Scale of the construction work.
  state     Permitted/Conditions 22   Approved schemes will actually get built.
            Undecided 9               Live, but may be refused.
  type      Outline 13                Earliest signal — contractors not yet appointed.
            Full 10                   New scheme, detailed design stage.
            Amendment 3               Changes to a scheme already in motion.
  dwellings up to 17 (1 per 10 units) Housing volume.
  recency   up to 13, decays over 18m Stale approvals have usually been let already.

  cap: 30   Low-value records (see `low_value_reason`) are capped at 30. Shown as a
            negative "cap" component.
  cap: 100  Defensive only: the five components above now sum to at most 100
            (35+22+13+17+13) by construction, so this branch should never fire on new
            scores. Kept in case a future weight change reopens the gap -- if it ever
            does trigger, it's shown as a second, disclosed negative "cap" component so
            the breakdown the UI displays always sums to the headline score — never
            silently clamped.

A record is low value, and hidden from the default view, for one of three reasons:
  "paperwork"    Follow-up paperwork on an existing scheme: a follow-up app_type, or a
                 description that opens with a paperwork phrase (PAPERWORK_PATTERN).
  "minor_works"  Householder-scale works (MINOR_WORKS_PATTERN).
  "duplicate"    A resubmission/amendment of another application already on record for the
                 same site (see harvester.mark_duplicate_schemes) -- large sites often collect
                 several outline/full applications over time as a scheme is revised, and
                 without this each resubmission showed up as its own separate, full-scored
                 "lead", inflating counts for what is really one physical development. Set at
                 harvest time, not by this module (it has no visibility into other rows).

Why caps rather than low weights: these records still collect size, dwellings and
recency points, and PlanIt's own size and type classifications are unreliable for them.
Measured on live data (2026-09):
  * Discharge of conditions is the most common app_type among Large schemes. On 40 live
    Large records a zero type weight alone left them averaging 78 against 85 for new
    Full/Outline schemes.
  * 514 of 1,575 "Medium" applications (33%) were householder works — rear extensions,
    porches, dormers, replacement windows — averaging a score of 69.
  * PlanIt also mistypes paperwork: 37% of "Amendment" records were non-material
    amendments, and "Details pursuant to condition…" submissions appear as Full or
    Outline. They averaged scores of 82-85.

Note the app_state "Conditions" (approved with conditions) is unrelated to the app_type
"Conditions" (discharge of conditions). PlanIt uses the same word for both.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

SIZE_POINTS = {"Large": 35, "Medium": 17}
STATE_POINTS = {"Permitted": 22, "Conditions": 22, "Undecided": 9}
TYPE_POINTS = {"Outline": 13, "Full": 10, "Amendment": 3}
DWELLING_POINTS_MAX = 17
DWELLINGS_PER_POINT = 10
RECENCY_POINTS_MAX = 13
RECENCY_WINDOW_DAYS = 548  # ~18 months
LOW_VALUE_CAP = 30

# app_types that are new opportunities.
OPPORTUNITY_APP_TYPES = ("Full", "Outline", "Amendment")
# Paperwork on an existing or minor scheme. "Other" is deliberately absent: PlanIt files
# agricultural and electrical schemes (solar farms, substations) there, which can be
# substantial. Unknown (null) app_type is also uncapped.
FOLLOW_UP_APP_TYPES = ("Conditions", "Heritage", "Trees", "Advertising", "Telecoms")

# Householder and similarly small works that PlanIt nonetheless sizes as Medium or Large.
# Checked against 25 random matches from live data: 22 were householder works and the
# other 3 small changes of use — none tender-scale. Only applied when the scheme has at
# most one dwelling, so "erection of 50 homes with single storey rear extensions" and
# similar genuine developments are never caught.
MINOR_WORKS_PATTERN = re.compile(
    r"(single|two|double|first floor|part single|part two)[- ]storey (rear|side|front|wrap)"
    r"|rear extension|side extension|loft conversion|dormer|conservatory|porch"
    r"|garage conversion|householder|roof[- ]mounted solar"
    r"|solar panels? (on|to) (the )?(roof|dwelling|house)"
    r"|replacement windows|outbuilding|garden room|annexe? (for|ancillary)|ev charging point",
    re.IGNORECASE,
)

# Paperwork that PlanIt types as Full, Outline or Amendment. Anchored to the start of the
# description: matching anywhere also caught genuine schemes that merely mention an
# earlier amendment or condition (e.g. "Construction of 186 residential units ...",
# a 172-unit care community). Against the unanchored version on live data, anchoring
# stopped flagging 5 genuine schemes and newly caught 5 paperwork submissions.
# Reserved Matters and variations of condition are deliberately not matched: both can
# carry real design changes.
PAPERWORK_PATTERN = re.compile(
    r"^\s*(section 96a )?(an? )?(application|request|submission)?( for| of)?( an?)?( the)?\s*"
    r"(approval of )?"
    r"(non[- ]?material amendments?|minor material amendment|details (of|pursuant to|reserved by|required by)"
    r"|(partial |re-?)?discharge of conditions?|approval of details reserved|compliance with conditions?)",
    re.IGNORECASE,
)

LOW_VALUE_REASONS = ("paperwork", "minor_works", "duplicate")


def low_value_reason(row: dict[str, Any]) -> str | None:
    """"paperwork", "minor_works", or None for a genuine opportunity.

    Uses the stored value when the row carries a low_value_reason key (even None);
    otherwise classifies from app_type, description and dwellings.
    """
    if "low_value_reason" in row:
        return row["low_value_reason"]
    description = row.get("description") or ""
    if row.get("app_type") in FOLLOW_UP_APP_TYPES or PAPERWORK_PATTERN.search(description):
        return "paperwork"
    # Guarded by dwellings so "50 homes with single storey rear extensions" is never caught.
    if (row.get("n_dwellings") or 0) <= 1 and MINOR_WORKS_PATTERN.search(description):
        return "minor_works"
    return None


def explain_lead_score(row: dict[str, Any], today: date | None = None) -> dict[str, int]:
    today = today or date.today()

    dwellings = row.get("n_dwellings") or 0
    dwelling_pts = min(DWELLING_POINTS_MAX, int(dwellings) // DWELLINGS_PER_POINT) if dwellings > 0 else 0

    ref = row.get("decided_date") or row.get("start_date")
    recency_pts = 0
    if isinstance(ref, date):
        age = (today - ref).days
        # A future date is a source error, not extra-fresh, so it earns nothing.
        if 0 <= age < RECENCY_WINDOW_DAYS:
            recency_pts = round(RECENCY_POINTS_MAX * (1 - age / RECENCY_WINDOW_DAYS))

    parts = {
        "size": SIZE_POINTS.get(row.get("app_size") or "", 0),
        "state": STATE_POINTS.get(row.get("app_state") or "", 0),
        "type": TYPE_POINTS.get(row.get("app_type") or "", 0),
        "dwellings": dwelling_pts,
        "recency": recency_pts,
    }
    subtotal = sum(parts.values())
    if low_value_reason(row):
        # Low-value records are capped down to 30, however high their raw subtotal.
        parts["cap"] = min(0, LOW_VALUE_CAP - subtotal)
    elif subtotal > 100:
        # Genuine large/approved/early-stage/high-dwelling/recent schemes can add up to
        # more than 100 (see module docstring). Disclose the clamp instead of letting the
        # breakdown sum to more than the headline score.
        parts["cap"] = 100 - subtotal
    else:
        parts["cap"] = 0
    return parts


def lead_score(row: dict[str, Any], today: date | None = None) -> int:
    # explain_lead_score() already clamps into [0, 100] via its "cap" component; this
    # max/min is a defensive floor/ceiling, not where the capping actually happens.
    return max(0, min(100, sum(explain_lead_score(row, today).values())))
