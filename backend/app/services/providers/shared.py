"""
Provider-agnostic prompt and output-schema definitions shared by every LLM
backend (Claude, Gemini, ...). Keeping this in one place means every
provider is held to exactly the same ground rules and produces the same
shaped output - see classifier.py's module docstring for the data
integrity rules this prompt enforces.

Two separate tasks live here, deliberately kept apart:
- Column mapping: the reviewer has already picked the target template
  (see main.py's /classify), so the LLM only maps columns into it. This
  keeps the prompt small - just one template's fields, not all five -
  which matters: an earlier version sent all five templates on every
  call and that prompt size alone was enough to hit capacity limits on
  some Gemini tiers (see README "Known limitations").
- Template suggestion: an optional, opt-in "not sure which template?"
  helper that still looks at every candidate template. Only used when
  the reviewer explicitly asks for a suggestion, not on every mapping.
"""

from __future__ import annotations

import json

from app.template_registry import TemplateSchema

SYSTEM_PROMPT = """You are helping migrate a customer's GRC (governance, risk, compliance) spreadsheet \
data into fixed CyberHQ CSV import templates.

Ground rules you must follow strictly:
1. You are proposing where each SOURCE COLUMN should map to in the TARGET TEMPLATE. You are not \
   allowed to invent, guess, infer, or fabricate data values - only classify and suggest structural mapping.
2. If a source column doesn't clearly correspond to any target field, set target_field to null rather \
   than forcing a weak match. Low-confidence guesses are fine (a human reviews everything) but must be \
   marked with an honestly low confidence score.
3. value_transform may only describe a pure FORMAT change (date format, list delimiter, case, units \
   already implied by the column) - never a change in meaning or a value lookup/substitution you are \
   not certain preserves the original meaning.
4. Every source column must appear exactly once in column_mappings, even if target_field is null.

Respond only by producing the structured output requested - no prose outside it."""

# Plain JSON Schema (not an SDK-specific wrapper) so it can be reused as-is
# for Claude's tool input_schema and Gemini's response_json_schema. Nullable
# fields use `anyOf` with an explicit null type rather than a `type` array,
# since that form is understood by both providers.
COLUMN_MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "column_mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_column": {"type": "string"},
                    "target_field": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Exact target column name from the template, or null if no reasonable field fits.",
                    },
                    "confidence": {"type": "number", "description": "0-1"},
                    "rationale": {
                        "type": "string",
                        "description": "Short reason for this mapping, referencing header name and/or sample values.",
                    },
                    "value_transform": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": (
                            "Plain-English description of any pure formatting change needed to fit the "
                            "target field's expected format (e.g. 'reformat date to DD/MM/YYYY', "
                            "'join list with ; instead of ,'). Must NEVER describe a change in meaning. "
                            "Null if the value can be copied as-is."
                        ),
                    },
                },
                "required": ["source_column", "target_field", "confidence", "rationale"],
            },
        },
    },
    "required": ["column_mappings"],
}

TEMPLATE_SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "best_template": {
            "type": "string",
            "description": "The template key that best matches this sheet's content.",
        },
        "template_confidence": {
            "type": "number",
            "description": "0-1 confidence that best_template is the right artefact type for this sheet.",
        },
        "template_rationale": {
            "type": "string",
            "description": "One or two sentences explaining why this template was chosen.",
        },
    },
    "required": ["best_template", "template_confidence"],
}


def format_template_for_prompt(t: TemplateSchema) -> str:
    lines = [f"### Template key: {t.key}  (label: {t.label})"]
    lines.append("Columns:")
    for col in t.columns:
        note = t.field_notes.get(col)
        allowed = t.allowed_values.get(col)
        extra = []
        if note:
            extra.append(note)
        if allowed:
            extra.append(f"Allowed values: {', '.join(allowed[:15])}")
        suffix = f" - {' | '.join(extra)}" if extra else ""
        lines.append(f"  - {col}{suffix}")
    if t.sample_rows:
        lines.append(f"Example row from the CyberHQ template itself: {json.dumps(t.sample_rows[0], default=str)}")
    return "\n".join(lines)


def build_mapping_prompt(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    target_template: TemplateSchema,
) -> str:
    return f"""Here is the CyberHQ target template to map into:

{format_template_for_prompt(target_template)}

---

Here is the SOURCE sheet to map. Sheet name: "{sheet_name}"

Source columns: {json.dumps(source_columns)}

Sample rows (up to 5):
{json.dumps(sample_rows, indent=2, default=str)}

Propose a mapping for every source column into the target template above. Call submit_mapping \
with your answer."""


def build_template_suggestion_prompt(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    candidate_templates: dict[str, TemplateSchema],
) -> str:
    templates_block = "\n\n".join(format_template_for_prompt(t) for t in candidate_templates.values())

    return f"""Here are the candidate CyberHQ target templates:

{templates_block}

---

Here is the SOURCE sheet to classify. Sheet name: "{sheet_name}"

Source columns: {json.dumps(source_columns)}

Sample rows (up to 5):
{json.dumps(sample_rows, indent=2, default=str)}

Work out which target template this sheet is most likely meant for - prefer the template whose \
column set and sample data most closely resembles the source sheet's actual content, not just \
similar-sounding names. Call submit_template_suggestion with your answer."""
