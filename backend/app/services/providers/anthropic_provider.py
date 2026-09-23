"""Claude (Anthropic) backend for sheet classification. See shared.py for
the provider-agnostic prompt/schema, and classifier.py for the dispatcher
that picks between this and the other providers."""

from __future__ import annotations

import os

from anthropic import Anthropic, APIStatusError

from app.template_registry import TemplateSchema

from .shared import (
    COLUMN_MAPPING_SCHEMA,
    CONTENT_RECOMMENDATION_SCHEMA,
    SYSTEM_PROMPT,
    TEMPLATE_SUGGESTION_SCHEMA,
    VALUE_CROSSWALK_SCHEMA,
    build_content_recommendation_prompt,
    build_mapping_prompt,
    build_template_suggestion_prompt,
    build_value_crosswalk_prompt,
)

LABEL = "Claude (Anthropic)"
KEY_ENV_VAR = "ANTHROPIC_API_KEY"
MODEL_ENV_VAR = "ANTHROPIC_MODEL"
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

MAPPING_TOOL = {
    "name": "submit_mapping",
    "description": "Submit the proposed column mapping for this sheet.",
    "input_schema": COLUMN_MAPPING_SCHEMA,
}

TEMPLATE_SUGGESTION_TOOL = {
    "name": "submit_template_suggestion",
    "description": "Submit which CyberHQ template best fits this sheet.",
    "input_schema": TEMPLATE_SUGGESTION_SCHEMA,
}

VALUE_CROSSWALK_TOOL = {
    "name": "submit_value_matches",
    "description": "Submit the best allowed-value match for each candidate value.",
    "input_schema": VALUE_CROSSWALK_SCHEMA,
}

CONTENT_RECOMMENDATION_TOOL = {
    "name": "submit_content_recommendations",
    "description": "Submit the best allowed-value recommendation for each row.",
    "input_schema": CONTENT_RECOMMENDATION_SCHEMA,
}


def _clean_error_message(e: Exception) -> str:
    """anthropic's APIStatusError stringifies to a raw JSON error body -
    surface just the human-readable message where we can."""
    if isinstance(e, APIStatusError) and isinstance(e.body, dict):
        message = e.body.get("error", {}).get("message")
        if message:
            return message
    return str(e)


def _call_tool(system_prompt: str, user_prompt: str, tool: dict, model: str) -> dict:
    api_key = os.environ.get(KEY_ENV_VAR)
    if not api_key:
        raise RuntimeError(f"{KEY_ENV_VAR} is not set. Add a Claude API key in Settings.")

    client = Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        raise RuntimeError(f"Claude request failed: {_clean_error_message(e)}") from e

    for block in response.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return block.input

    raise RuntimeError(f"Claude did not return a {tool['name']} tool call.")


def classify_sheet(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    target_template: TemplateSchema,
    model: str | None = None,
) -> dict:
    model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    user_prompt = build_mapping_prompt(sheet_name, source_columns, sample_rows, target_template)
    return _call_tool(SYSTEM_PROMPT, user_prompt, MAPPING_TOOL, model)


def suggest_template(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    candidate_templates: dict[str, TemplateSchema],
    model: str | None = None,
) -> dict:
    model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    user_prompt = build_template_suggestion_prompt(sheet_name, source_columns, sample_rows, candidate_templates)
    return _call_tool(SYSTEM_PROMPT, user_prompt, TEMPLATE_SUGGESTION_TOOL, model)


def match_column_values(
    field_name: str,
    allowed_values: list[str],
    candidates: list[str],
    model: str | None = None,
) -> dict:
    model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    user_prompt = build_value_crosswalk_prompt(field_name, allowed_values, candidates)
    return _call_tool(SYSTEM_PROMPT, user_prompt, VALUE_CROSSWALK_TOOL, model)


def recommend_from_content(
    field_name: str,
    allowed_values: list[str],
    row_contents: list[str],
    model: str | None = None,
) -> dict:
    model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    user_prompt = build_content_recommendation_prompt(field_name, allowed_values, row_contents)
    return _call_tool(SYSTEM_PROMPT, user_prompt, CONTENT_RECOMMENDATION_TOOL, model)


def test_key(api_key: str) -> None:
    """Raise if this key doesn't work. A cheap, non-generating call used to
    validate a key before it's saved to .env."""
    client = Anthropic(api_key=api_key)
    try:
        client.models.list(limit=1)
    except Exception as e:
        raise RuntimeError(f"Claude rejected this key: {_clean_error_message(e)}") from e
