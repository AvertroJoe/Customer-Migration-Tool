"""Reads an uploaded customer spreadsheet (csv/xlsx) into one DataFrame per sheet.

Everything is read as strings with no NA coercion, so we never silently
turn e.g. a blank cell into NaN-that-prints-weird, or a leading-zero code
into a stripped number. Formatting/typing is only ever touched later, in
exporter.py, and only via an explicit transform the reviewer chose.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_sheets(path: Path) -> dict[str, pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        return {"Sheet1": df}
    if suffix in (".xlsx", ".xlsm"):
        sheets = pd.read_excel(path, sheet_name=None, dtype=str, keep_default_na=False, engine="openpyxl")
        # Drop fully-empty sheets (no columns or no rows) - nothing useful to migrate there.
        return {name: df for name, df in sheets.items() if len(df.columns) > 0 and len(df) > 0}
    raise ValueError(f"Unsupported file type: {suffix}. Please upload a .csv or .xlsx file.")
