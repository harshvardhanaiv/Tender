from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from .fields import flatten_for_export, order_export_columns

ExportFormat = Literal["csv", "xlsx"]


def rows_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    normalized = flatten_for_export(rows)
    if not normalized:
        return pd.DataFrame()
    columns = order_export_columns(list(normalized[0].keys()))
    return pd.DataFrame(normalized, columns=columns)


def write_output(rows: list[dict[str, Any]], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = rows_to_dataframe(rows)

    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    elif suffix in {".xlsx", ".xls"}:
        frame.to_excel(path, index=False, sheet_name="tenders")
    else:
        raise ValueError(f"Unsupported output format: {suffix}. Use .csv or .xlsx")

    return path


def export_bytes(rows: list[dict[str, Any]], fmt: ExportFormat) -> bytes:
    frame = rows_to_dataframe(rows)
    buffer = io.BytesIO()
    if fmt == "csv":
        frame.to_csv(buffer, index=False, encoding="utf-8-sig")
    else:
        frame.to_excel(buffer, index=False, sheet_name="tenders")
    return buffer.getvalue()
