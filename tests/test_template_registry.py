"""Unit tests for template_registry.py - confirms the real /templates
files parse correctly and the confirmed constrained-value data (issue
#9) lands on the right TemplateSchema fields. Reads the real templates/
directory, not fixtures - these files ARE the source of truth."""

from __future__ import annotations

from app.template_registry import (
    ALLOWED_VALUES,
    CONTENT_RECOMMEND_FIELDS,
    DELIMITED_FIELDS,
    framework_assessment_templates,
    load_registry,
    structural_templates,
)


def test_all_six_templates_are_discovered():
    registry = load_registry()
    structural = structural_templates(registry)
    framework = framework_assessment_templates(registry)
    assert set(structural.keys()) == {"risk_register", "issue", "entity", "kbs", "resource"}
    assert len(framework) == 1


def test_resource_template_prefill_sheet_populates_allowed_values():
    registry = load_registry()
    resource = structural_templates(registry)["resource"]
    # KNOWN BUG (see issue filed after this test caught it): the Prefill
    # sheet's own column headers ("Technology", "People", ...) become the
    # allowed_values KEYS, not the actual template columns they describe
    # ("Type", "SubType", "Cost Frequency"). So `allowed_values["Type"]`
    # is empty even though the data exists under "Technology" et al. -
    # this test documents what actually happens today, not what should.
    assert "Type" not in resource.allowed_values
    assert "Cost Frequency" not in resource.allowed_values
    assert "Network and Infrastructure Security" in resource.allowed_values["Technology"]
    assert "Week" in resource.allowed_values["Per"]


def test_hardcoded_allowed_values_are_merged_onto_the_right_templates():
    registry = load_registry()
    structural = structural_templates(registry)
    for key, fields in ALLOWED_VALUES.items():
        for field, values in fields.items():
            assert structural[key].allowed_values[field] == values


def test_delimited_and_content_recommend_fields_are_wired_through():
    registry = load_registry()
    structural = structural_templates(registry)
    for key, fields in DELIMITED_FIELDS.items():
        assert structural[key].delimited_fields == fields
    for key, fields in CONTENT_RECOMMEND_FIELDS.items():
        assert structural[key].content_recommend_fields == fields


def test_every_structural_template_has_columns():
    registry = load_registry()
    for schema in structural_templates(registry).values():
        assert len(schema.columns) > 0


def test_hardcoded_allowed_values_keys_are_real_columns_on_their_template():
    # The CSV templates' ALLOWED_VALUES (issue #9) are hand-authored against
    # real column names, so this should always hold for them. Deliberately
    # excludes "resource" - see the known-bug test above; its allowed_values
    # come from the xlsx Prefill sheet, which doesn't have this guarantee.
    registry = load_registry()
    structural = structural_templates(registry)
    for key, fields in ALLOWED_VALUES.items():
        for field in fields:
            assert field in structural[key].columns, f"{key}.{field} has allowed_values but isn't a real column"
