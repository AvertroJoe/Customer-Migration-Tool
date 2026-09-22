"""
Calls Claude to (1) work out which CyberHQ structural template a source
sheet most resembles, and (2) propose a column-to-field mapping with a
confidence score and a plain-English rationale for each column.

This module never touches the actual source data values - it only ever
sees column headers and a handful of sample rows, and only ever proposes
where a whole column should go. Nothing here writes the final CSV; that
happens in exporter.py, only after a human has approved (and can edit)
what this module proposes. See README "How data integrity is preserved".
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from anthropic import Anthropic

from app.template_registry import TemplateSchema

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file (see .env.example)."
            )
        _client = Anthropic(api_key=api_key)
    return _client


MAPPING_TOOL = {
    "name": "submit_mapping",
    "description": "Submit the template classification and column mapping for this sheet.",
    "input_schema": {
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
            "column_mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_column": {"type": "string"},
                        "target_field": {
                            "type": ["string", "null"],
                            "description": "Exact target column name from the template, or null if no reasonable field fits.",
                        },
                        "confidence": {"type": "number", "description": "0-1"},
                        "rationale": {
                            "type": "string",
                            "description": "Short reason for this mapping, referencing header name and/or sample values.",
                        },
                        "value_transform": {
                            "type": ["string", "null"],
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
        "required": ["best_template", "template_confidence", "column_mappings"],
    },
}

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
5. Prefer the template whose column set and sample data most closely resembles the source sheet's \
   actual content, not just similar-sounding names.
"""


def _format_template_for_prompt(t: TemplateSchema) -> str:
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


def classify_sheet(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    candidate_templates: dict[str, TemplateSchema],
    model: str | None = None,
) -> dict:
    """Ask Claude to classify one source sheet against the candidate structural templates."""

    model = model or os.environ.get("CLASSIFIER_MODEL", "claude-sonnet-4-5-20250929")

    templates_block = "\n\n".join(_format_template_for_prompt(t) for t in candidate_templates.values())

    user_prompt = f"""Here are the candidate CyberHQ target templates:

{templates_block}

---

Here is the SOURCE sheet to classify and map. Sheet name: "{sheet_name}"

Source columns: {json.dumps(source_columns)}

Sample rows (up to 5):
{json.dumps(sample_rows, indent=2, default=str)}

Work out which target template this sheet is most likely meant for, then propose a mapping for \
every source column. Call submit_mapping with your answer."""

    client = get_client()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[MAPPING_TOOL],
        tool_choice={"type": "tool", "name": "submit_mapping"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_mapping":
            return block.input

    raise RuntimeError("Model did not return a submit_mapping tool call.")
