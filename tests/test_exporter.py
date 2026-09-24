"""Unit tests for exporter.py - the only module that writes the final
CSV. These are the exact cases hand-verified as one-off scripts while
building issue #9 (constrained-field value mapping), turned into real,
repeatable tests. See README "Constrained-field value mapping" and
"Data integrity, by design" for the rules this file protects."""

from __future__ import annotations

import pandas as pd
import pytest

from app.services.exporter import (
    MappingRow,
    UnresolvedColumnsError,
    build_export,
    split_delimited_value,
)
from app.template_registry import TemplateSchema


def _template(columns: list[str]) -> TemplateSchema:
    return TemplateSchema(key="issue", label="Issue Register", kind="structural", source_file="x.csv", columns=columns)


class TestBasicMapping:
    def test_plain_mapping_unchanged_from_before_issue_9(self):
        target = _template(["Title", "Status"])
        df = pd.DataFrame({"A": ["hello"], "B": ["world"]})
        mapping = [MappingRow("A", "Title"), MappingRow("B", "Status")]
        out, warnings = build_export(df, mapping, target)
        assert out.to_dict(orient="records") == [{"Title": "hello", "Status": "world"}]
        assert warnings == []

    def test_unmapped_source_column_without_catch_all_or_drop_raises(self):
        target = _template(["Title"])
        df = pd.DataFrame({"A": ["hello"], "B": ["world"]})
        mapping = [MappingRow("A", "Title"), MappingRow("B", None)]
        with pytest.raises(UnresolvedColumnsError) as exc_info:
            build_export(df, mapping, target)
        assert exc_info.value.columns == ["B"]

    def test_source_column_missing_from_mapping_entirely_raises(self):
        target = _template(["Title"])
        df = pd.DataFrame({"A": ["hello"], "B": ["world"]})
        mapping = [MappingRow("A", "Title")]  # B never mentioned at all
        with pytest.raises(ValueError, match="not included in the mapping"):
            build_export(df, mapping, target)

    def test_catch_all_field_collects_unmapped_columns(self):
        target = _template(["Title", "Other Fields"])
        df = pd.DataFrame({"A": ["hello"], "B": ["extra info"]})
        mapping = [MappingRow("A", "Title"), MappingRow("B", None)]
        out, _ = build_export(df, mapping, target, catch_all_field="Other Fields")
        assert out.iloc[0]["Other Fields"] == "B: extra info"

    def test_confirmed_drop_produces_a_warning_not_silence(self):
        target = _template(["Title"])
        df = pd.DataFrame({"A": ["hello"], "B": ["gone"]})
        mapping = [MappingRow("A", "Title"), MappingRow("B", None)]
        out, warnings = build_export(df, mapping, target, confirmed_drop_columns=["B"])
        assert "B" not in out.columns
        assert any("confirmed dropped" in w for w in warnings)


class TestValueMap:
    def test_value_map_translates_and_unmatched_value_is_passed_through_and_warned(self):
        target = _template(["Title", "Status"])
        df = pd.DataFrame({"IssueName": ["A", "B", "C"], "State": ["Review", "Respond", "Mystery"]})
        mapping = [
            MappingRow("IssueName", "Title"),
            MappingRow("State", "Status", value_map={"Review": "Open", "Respond": "In Progress"}),
        ]
        out, warnings = build_export(df, mapping, target)
        assert out["Status"].tolist() == ["Open", "In Progress", "Mystery"]
        assert any("Mystery" in w for w in warnings)

    def test_value_map_lookup_is_case_and_whitespace_insensitive(self):
        target = _template(["Status"])
        df = pd.DataFrame({"State": ["  review  ", "REVIEW"]})
        mapping = [MappingRow("State", "Status", value_map={"Review": "Open"})]
        out, warnings = build_export(df, mapping, target)
        assert out["Status"].tolist() == ["Open", "Open"]
        assert warnings == []

    def test_delimited_field_splits_maps_and_rejoins_tokens(self):
        target = _template(["Name", "Data Stored"])
        df = pd.DataFrame({"Sys": ["S1"], "DataTypes": ["PII;Financial Records;PII"]})
        mapping = [
            MappingRow("Sys", "Name"),
            MappingRow("DataTypes", "Data Stored", value_map={"PII": "Personally Identifiable Information (PII)"}),
        ]
        out, warnings = build_export(df, mapping, target, delimited_fields=["Data Stored"])
        assert out["Data Stored"].tolist() == [
            "Personally Identifiable Information (PII); Financial Records; "
            "Personally Identifiable Information (PII)"
        ]
        assert any("Financial Records" in w for w in warnings)

    def test_non_delimited_field_never_splits_on_semicolon(self):
        # A value_map'd field NOT marked delimited must treat a ';' in the
        # raw value as part of one token, never split it - even though
        # split_delimited_value() would split it if called directly.
        target = _template(["Status"])
        df = pd.DataFrame({"State": ["Weird;Value"]})
        # Non-empty but non-matching map, so the crosswalk path actually runs
        # (an empty dict is falsy and would skip it entirely).
        mapping = [MappingRow("State", "Status", value_map={"Unrelated": "X"})]
        out, warnings = build_export(df, mapping, target)  # delimited_fields not passed
        assert out["Status"].tolist() == ["Weird;Value"]
        assert any("Weird;Value" in w for w in warnings)


class TestDefaultsAndRowValues:
    def test_field_defaults_fill_every_row_for_an_unmapped_field(self):
        target = _template(["Title", "Priority"])
        df = pd.DataFrame({"A": ["one", "two"]})
        mapping = [MappingRow("A", "Title")]
        out, _ = build_export(df, mapping, target, field_defaults={"Priority": "Medium"})
        assert out["Priority"].tolist() == ["Medium", "Medium"]

    def test_field_row_values_apply_per_row_in_order(self):
        target = _template(["Title", "Type"])
        df = pd.DataFrame({"A": ["one", "two", "three"]})
        mapping = [MappingRow("A", "Title")]
        out, _ = build_export(df, mapping, target, field_row_values={"Type": ["Threat", "Other", "Vulnerability"]})
        assert out["Type"].tolist() == ["Threat", "Other", "Vulnerability"]

    def test_field_row_values_length_mismatch_raises(self):
        target = _template(["Title", "Type"])
        df = pd.DataFrame({"A": ["one", "two"]})
        mapping = [MappingRow("A", "Title")]
        with pytest.raises(ValueError, match="rows"):
            build_export(df, mapping, target, field_row_values={"Type": ["Threat"]})

    def test_field_in_both_mapping_and_default_raises_rather_than_silently_choosing(self):
        target = _template(["Title", "Type"])
        df = pd.DataFrame({"A": ["one"], "B": ["two"]})
        mapping = [MappingRow("A", "Title"), MappingRow("B", "Type")]
        with pytest.raises(ValueError, match="Type"):
            build_export(df, mapping, target, field_defaults={"Type": "Threat"})

    def test_field_in_both_mapping_and_row_values_raises(self):
        target = _template(["Title", "Type"])
        df = pd.DataFrame({"A": ["one"], "B": ["two"]})
        mapping = [MappingRow("A", "Title"), MappingRow("B", "Type")]
        with pytest.raises(ValueError, match="Type"):
            build_export(df, mapping, target, field_row_values={"Type": ["Threat"]})


class TestSplitDelimitedValue:
    def test_splits_trims_and_drops_blanks(self):
        assert split_delimited_value("a; b ;;c") == ["a", "b", "c"]

    def test_none_and_blank_return_empty_list(self):
        assert split_delimited_value(None) == []
        assert split_delimited_value("   ") == []

    def test_single_value_with_no_delimiter(self):
        assert split_delimited_value("just one") == ["just one"]
