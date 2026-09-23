"""
Builds the final CyberHQ-import-ready CSV from a human-approved mapping.

This is the only module that writes the output file, and it is
deliberately dumb: it moves and reformats values exactly as the reviewer
told it to, and never infers, invents, or drops data silently. Every
source column must be accounted for - either mapped to a target field, or
explicitly routed to a catch-all field, or explicitly confirmed as
dropped by the caller (never the default).

Constrained fields (a fixed set of allowed values, e.g. Status) follow
the same principle - see issue #9. A reviewer-approved value_map,
field_defaults, or field_row_values is applied exactly as given; a value
with nowhere to go is passed through unchanged AND surfaced as a warning,
never silently dropped or guessed.
"""

from __future__ import annotations

import re
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

_DELIMITER_SPLIT = re.compile(r"\s*;\s*")


def split_delimited_value(raw_value) -> list[str]:
    """Splits a ';'-delimited cell into its individual tokens (trimmed,
    blanks dropped). Public so callers building crosswalk candidates
    (see main.py's /crosswalk-values) split values the exact same way
    _apply_value_map does at export time."""
    raw = "" if raw_value is None else str(raw_value).strip()
    if not raw:
        return []
    return [t for t in _DELIMITER_SPLIT.split(raw) if t]


@dataclass
class MappingRow:
    source_column: str
    target_field: str | None  # None / "" means unmapped
    transform: str = "none"
    # source value -> target value, for a constrained target field (issue #9).
    # Approved against raw source values, so when present it takes precedence
    # over `transform` for this column - applying both would be undefined
    # (the crosswalk already produces the canonical target value).
    value_map: dict[str, str] | None = None


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


def _apply_value_map(raw_value, value_map: dict[str, str], delimited: bool) -> tuple[str, list[str]]:
    """Returns (mapped_value, unmatched_tokens). A token not found in
    value_map is passed through unchanged and reported as unmatched -
    never silently dropped."""
    normalized = {k.strip().casefold(): v for k, v in value_map.items()}
    if delimited:
        tokens = split_delimited_value(raw_value)
    else:
        # Not delimited - treat the whole cell as one token, even if it
        # happens to contain a ';' (never split data the reviewer didn't
        # ask us to split).
        raw = "" if raw_value is None else str(raw_value).strip()
        tokens = [raw] if raw else []

    mapped_tokens = []
    unmatched = []
    for token in tokens:
        match = normalized.get(token.casefold())
        if match is not None:
            mapped_tokens.append(match)
        else:
            mapped_tokens.append(token)
            unmatched.append(token)

    return "; ".join(mapped_tokens), unmatched


def build_export(
    source_df: pd.DataFrame,
    mapping: list[MappingRow],
    target: TemplateSchema,
    catch_all_field: str | None = None,
    confirmed_drop_columns: list[str] | None = None,
    field_defaults: dict[str, str] | None = None,
    field_row_values: dict[str, list[str]] | None = None,
    delimited_fields: list[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Returns (output_df, warnings).

    Raises UnresolvedColumnsError if any source column has no target_field,
    no catch_all_field configured, and isn't in confirmed_drop_columns -
    i.e. the caller must make an explicit decision for every column before
    data can be lost.

    field_defaults and field_row_values fill target fields that have no
    mapped source column - a constant applied to every row, or one value
    per row (e.g. a content-based recommendation), respectively. Neither
    may target a field that's also in the mapping - see the collision
    check below; that ambiguity must be resolved by the caller, not
    silently arbitrated here.
    """
    warnings: list[str] = []
    confirmed_drop_columns = set(confirmed_drop_columns or [])
    field_defaults = field_defaults or {}
    field_row_values = field_row_values or {}
    delimited_fields = set(delimited_fields or [])

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

    # target_field -> list of (source_column, transform, value_map) - support many-to-one
    field_sources: dict[str, list[tuple[str, str, dict | None]]] = {}
    catch_all_sources: list[str] = []
    for m in mapping:
        if m.target_field:
            field_sources.setdefault(m.target_field, []).append((m.source_column, m.transform, m.value_map))
        elif catch_all_field and m.source_column not in confirmed_drop_columns:
            catch_all_sources.append(m.source_column)
        elif m.source_column in confirmed_drop_columns:
            warnings.append(f"Column '{m.source_column}' was confirmed dropped and is not in the output.")

    for target_field, sources in field_sources.items():
        if len(sources) > 1:
            warnings.append(
                f"Target field '{target_field}' received {len(sources)} source columns "
                f"({[s for s, _, _ in sources]}) - values were joined with '; '."
            )

    # A field can only be filled one way - a mapped source column, a
    # constant default, or per-row recommended values. Silently letting
    # one win would be exactly the kind of unreviewed decision this tool
    # is built to avoid.
    colliding = (set(field_defaults) | set(field_row_values)) & set(field_sources)
    if colliding:
        raise ValueError(
            f"These target fields have both a mapped source column and a default/recommended "
            f"value - resolve which one should apply: {sorted(colliding)}"
        )
    for target_field, values in field_row_values.items():
        if len(values) != len(source_df):
            raise ValueError(
                f"field_row_values for '{target_field}' has {len(values)} entries, "
                f"but the sheet has {len(source_df)} rows."
            )

    unmatched_by_field: dict[str, set[str]] = {}
    source_df = source_df.reset_index(drop=True)

    output_rows = []
    for row_idx, row in source_df.iterrows():
        out_row = {col: field_defaults.get(col, "") for col in target.columns}

        for target_field, values in field_row_values.items():
            out_row[target_field] = values[row_idx]

        for target_field, sources in field_sources.items():
            parts = []
            for source_col, transform, value_map in sources:
                raw = row.get(source_col, "")
                if value_map:
                    val, unmatched = _apply_value_map(raw, value_map, target_field in delimited_fields)
                    if unmatched:
                        unmatched_by_field.setdefault(target_field, set()).update(unmatched)
                else:
                    val = _apply_transform(raw, transform)
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

    for target_field, values in sorted(unmatched_by_field.items()):
        warnings.append(
            f"Target field '{target_field}': {len(values)} source value(s) didn't match any "
            f"allowed value and were passed through unchanged - {sorted(values)}"
        )

    output_df = pd.DataFrame(output_rows, columns=target.columns)
    return output_df, warnings
