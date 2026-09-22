"""
Builds the final CyberHQ-import-ready CSV from a human-approved mapping.

This is the only module that writes the output file, and it is
deliberately dumb: it moves and reformats values exactly as the reviewer
told it to, and never infers, invents, or drops data silently. Every
source column must be accounted for - either mapped to a target field, or
explicitly routed to a catch-all field, or explicitly confirmed as
dropped by the caller (never the default).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from dateutil import parser as dateutil_parser

from app.template_registry import TemplateSchema

SUPPORTED_TRANSFORMS = {
    "none",
    "trim",
    "parse_date_ddmmyyyy",
    "parse_date_yyyymmdd",
    "uppercase",
    "lowercase",
}


@dataclass
class MappingRow:
    source_column: str
    target_field: str | None  # None / "" means unmapped
    transform: str = "none"


class UnresolvedColumnsError(Exception):
    """Raised when source columns are unmapped and not explicitly confirmed as dropped."""

    def __init__(self, columns: list[str]):
        self.columns = columns
        super().__init__(
            f"{len(columns)} source column(s) are unmapped and not confirmed for drop: {columns}"
        )


def _apply_transform(value, transform: str):
    if value is None:
        return ""
    value = str(value)
    if transform == "none" or not transform:
        return value
    if transform == "trim":
        return value.strip()
    if transform == "uppercase":
        return value.strip().upper()
    if transform == "lowercase":
        return value.strip().lower()
    if transform in ("parse_date_ddmmyyyy", "parse_date_yyyymmdd"):
        if not value.strip():
            return ""
        try:
            dt = dateutil_parser.parse(value, dayfirst=True)
        except (ValueError, OverflowError):
            # Can't parse - return original value untouched rather than guessing.
            return value
        if transform == "parse_date_ddmmyyyy":
            return dt.strftime("%d/%m/%Y")
        return dt.strftime("%Y-%m-%d")
    raise ValueError(f"Unsupported transform: {transform}")


def build_export(
    source_df: pd.DataFrame,
    mapping: list[MappingRow],
    target: TemplateSchema,
    catch_all_field: str | None = None,
    confirmed_drop_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Returns (output_df, warnings).

    Raises UnresolvedColumnsError if any source column has no target_field,
    no catch_all_field configured, and isn't in confirmed_drop_columns -
    i.e. the caller must make an explicit decision for every column before
    data can be lost.
    """
    warnings: list[str] = []
    confirmed_drop_columns = set(confirmed_drop_columns or [])

    mapped_source_cols = {m.source_column for m in mapping}
    missing_from_mapping = [c for c in source_df.columns if c not in mapped_source_cols]
    if missing_from_mapping:
        raise ValueError(
            f"These source columns were not included in the mapping at all: {missing_from_mapping}"
        )

    unresolved = []
    for m in mapping:
        if not m.target_field:
            if catch_all_field and m.source_column not in confirmed_drop_columns:
                continue  # will be folded into catch_all_field below
            if m.source_column not in confirmed_drop_columns:
                unresolved.append(m.source_column)

    if unresolved:
        raise UnresolvedColumnsError(unresolved)

    for m in mapping:
        if m.transform not in SUPPORTED_TRANSFORMS:
            raise ValueError(f"Unsupported transform '{m.transform}' for column '{m.source_column}'")
        if m.target_field and m.target_field not in target.columns:
            raise ValueError(
                f"target_field '{m.target_field}' is not a column in template '{target.key}'"
            )

    # target_field -> list of (source_column, transform) - support many-to-one
    field_sources: dict[str, list[tuple[str, str]]] = {}
    catch_all_sources: list[str] = []
    for m in mapping:
        if m.target_field:
            field_sources.setdefault(m.target_field, []).append((m.source_column, m.transform))
        elif catch_all_field and m.source_column not in confirmed_drop_columns:
            catch_all_sources.append(m.source_column)
        elif m.source_column in confirmed_drop_columns:
            warnings.append(f"Column '{m.source_column}' was confirmed dropped and is not in the output.")

    for target_field, sources in field_sources.items():
        if len(sources) > 1:
            warnings.append(
                f"Target field '{target_field}' received {len(sources)} source columns "
                f"({[s for s, _ in sources]}) - values were joined with '; '."
            )

    output_rows = []
    for _, row in source_df.iterrows():
        out_row = {col: "" for col in target.columns}
        for target_field, sources in field_sources.items():
            parts = []
            for source_col, transform in sources:
                val = _apply_transform(row.get(source_col, ""), transform)
                if val != "":
                    parts.append(val)
            out_row[target_field] = "; ".join(parts)

        if catch_all_field and catch_all_sources:
            notes = []
            for source_col in catch_all_sources:
                val = row.get(source_col, "")
                if val not in (None, ""):
                    notes.append(f"{source_col}: {val}")
            if notes:
                existing = out_row.get(catch_all_field, "")
                combined = "; ".join(([existing] if existing else []) + notes)
                out_row[catch_all_field] = combined

        output_rows.append(out_row)

    output_df = pd.DataFrame(output_rows, columns=target.columns)
    return output_df, warnings
