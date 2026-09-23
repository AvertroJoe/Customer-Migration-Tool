"""Reads an uploaded customer spreadsheet (csv/xlsx) into raw rows, then -
once the reviewer confirms which row is the real header - into one
DataFrame per sheet.

Everything is read as strings with no NA coercion, so we never silently
turn e.g. a blank cell into NaN-that-prints-weird, or a leading-zero code
into a stripped number. Formatting/typing is only ever touched later, in
exporter.py, and only via an explicit transform the reviewer chose.

Real customer sheets often have a row or two of free-text title/context
above the actual header row (see README "Header row detection"), so
uploading never assumes row 1 is the header. Instead: read_raw_sheets()
gives every row as-is, guess_header_row() offers a best-effort guess for
where the real header starts, and the reviewer confirms (or corrects) it
in the UI before build_dataframe() turns it into the DataFrame the rest
of the app works with. Nothing here decides that automatically - a wrong
guess would silently corrupt every downstream mapping.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# How many of a sheet's leading rows are worth scanning when guessing where
# the header starts. Real "informational" preambles (title, instructions,
# a blank spacer row) are almost always within the first handful of rows -
# scanning further just risks matching a False positive deeper in the data.
MAX_HEADER_SCAN_ROWS = 20


def _row_is_blank(row: list[str]) -> bool:
    return all(cell == "" for cell in row)


def read_raw_sheets(path: Path) -> dict[str, list[list[str]]]:
    """Read every sheet as a raw grid of string cells, header row unknown,
    with blank rows kept in place - so "row 3" in this grid is row 3 in
    the reviewer's own spreadsheet, not shifted by rows we quietly
    dropped. Only fully-empty sheets (no non-blank row at all) are
    dropped - nothing useful to migrate there."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, header=None, dtype=str, keep_default_na=False)
        sheets = {"Sheet1": df}
    elif suffix in (".xlsx", ".xlsm"):
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str, keep_default_na=False, engine="openpyxl")
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Please upload a .csv or .xlsx file.")

    result: dict[str, list[list[str]]] = {}
    for name, df in sheets.items():
        rows = df.values.tolist()
        if any(not _row_is_blank(row) for row in rows):
            result[name] = rows
    return result


def guess_header_row(rows: list[list[str]]) -> int:
    """Best-effort guess at which row (0-indexed) is the real header.

    A title/instructions row is typically narrow - one or two populated
    cells, the rest blank - while the real header row is close to the
    sheet's full width and is followed by more rows of similar width.
    Scans top-down and returns the first "wide enough" row, since any
    preamble rows come before the tabular section, never inside it.
    Falls back to row 0 if nothing looks conclusive (e.g. a sheet that's
    already headers-first, or too irregular to guess confidently).
    """
    scanned = rows[:MAX_HEADER_SCAN_ROWS]
    widths = [sum(1 for cell in row if cell != "") for row in scanned]
    max_width = max(widths, default=0)
    if max_width < 2:
        return 0

    threshold = max(2, round(max_width * 0.6))
    for i, width in enumerate(widths):
        if width < threshold:
            continue
        # Confirm it's followed by more tabular content, not an isolated
        # wide row with nothing beneath it.
        if i + 1 < len(rows) and not _row_is_blank(rows[i + 1]):
            return i
        if i + 1 >= len(rows):
            return i

    return 0


def build_dataframe(rows: list[list[str]], header_row_index: int) -> pd.DataFrame:
    """Turn raw rows into a DataFrame using rows[header_row_index] as column
    names and everything after as data. Blank header cells and duplicate
    names are given stable, human-readable fallback names rather than
    failing or silently overwriting a column."""
    if header_row_index < 0 or header_row_index >= len(rows):
        raise ValueError(f"Header row {header_row_index + 1} is out of range for this sheet.")

    raw_header = rows[header_row_index]
    seen: dict[str, int] = {}
    columns: list[str] = []
    for i, cell in enumerate(raw_header):
        name = cell.strip() or f"Column {i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        columns.append(name)

    data_rows = [row for row in rows[header_row_index + 1 :] if not _row_is_blank(row)]
    width = len(columns)
    normalised = [
        (row + [""] * width)[:width] if len(row) != width else row
        for row in data_rows
    ]
    return pd.DataFrame(normalised, columns=columns, dtype=str)
