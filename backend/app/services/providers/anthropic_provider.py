"""Claude (Anthropic) backend for sheet classification. See shared.py for
the provider-agnostic prompt/schema, and classifier.py for the dispatcher
that picks between this and the other providers."""

from __future__ import annotations

import os

from anthropic import Anthropic, APIStatusError

from app.template_registry import TemplateSchema

from .shared import MAPPING_SCHEMA, SYSTEM_PROMPT, build_user_prompt


def _clean_error_message(e: Exception) -> str:
    """anthropic's APIStatusError stringifies to a raw JSON error body -
    surface just the human-readable message where we can."""
    if isinstance(e, APIStatusError) and isinstance(e.body, dict):
        message = e.body.get("error", {}).get("message")
        if message:
            return message
    return str(e)

LABEL = "Claude (Anthropic)"
KEY_ENV_VAR = "ANTHROPIC_API_KEY"
MODEL_ENV_VAR = "ANTHROPIC_MODEL"
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

MAPPING_TOOL = {
    "name": "submit_mapping",
    "description": "Submit the template classification and column mapping for this sheet.",
    "input_schema": MAPPING_SCHEMA,
}


def classify_sheet(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    candidate_templates: dict[str, TemplateSchema],
    model: str | None = None,
) -> dict:
    api_key = os.environ.get(KEY_ENV_VAR)
    if not api_key:
        raise RuntimeError(f"{KEY_ENV_VAR} is not set. Add a Claude API key in Settings.")

    model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    user_prompt = build_user_prompt(sheet_name, source_columns, sample_rows, candidate_templates)

    client = Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[MAPPING_TOOL],
            tool_choice={"type": "tool", "name": "submit_mapping"},
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        raise RuntimeError(f"Claude request failed: {_clean_error_message(e)}") from e

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_mapping":
            return block.input

    raise RuntimeError("Claude did not return a submit_mapping tool call.")


def test_key(api_key: str) -> None:
    """Raise if this key doesn't work. A cheap, non-generating call used to
    validate a key before it's saved to .env."""
    client = Anthropic(api_key=api_key)
    try:
        client.models.list(limit=1)
    except Exception as e:
        raise RuntimeError(f"Claude rejected this key: {_clean_error_message(e)}") from e
