"""Shared SQL radius-filter helper for tables that carry latitude/longitude.

Used by planning_bp (planning_applications) and suppliers_bp (suppliers). Buyer Intelligence
has its own separate, unrelated distance mechanism (buyers_bp.haversine_km, applied in Python
over an already-fetched feed) and does not use this module.
"""
from __future__ import annotations

import math
from typing import Any


def geo_radius_clause(lat: float, lng: float, radius_km: float,
                       lat_col: str = "latitude", lon_col: str = "longitude") -> dict[str, Any]:
    """Radius filter as SQL plus its params, kept together so callers cannot misorder them.

    Bounding-box prefilter, then equirectangular distance — accurate to well under 1% at UK
    latitudes and radii, and needs no PostGIS.

    Returns where_sql/where_params (for WHERE) and distance_sql/distance_params (for a SELECT
    column, if the caller wants to return the distance too).
    """
    dlat = radius_km / 111.32
    dlng = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    distance_sql = (
        f"(111.32 * sqrt(power({lat_col} - %s, 2) + "
        f"power(({lon_col} - %s) * cos(radians(%s)), 2)))"
    )
    distance_params = [lat, lng, lat]
    return {
        "where_sql": f"{lat_col} BETWEEN %s AND %s AND {lon_col} BETWEEN %s AND %s AND {distance_sql} <= %s",
        "where_params": [lat - dlat, lat + dlat, lng - dlng, lng + dlng, *distance_params, radius_km],
        "distance_sql": distance_sql,
        "distance_params": distance_params,
    }
