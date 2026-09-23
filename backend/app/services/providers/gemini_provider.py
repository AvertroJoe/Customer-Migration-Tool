"""Gemini (Google) backend for sheet classification. See shared.py for
the provider-agnostic prompt/schema, and classifier.py for the dispatcher
that picks between this and the other providers."""

from __future__ import annotations

import json
import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.template_registry import TemplateSchema

from .shared import (
    COLUMN_MAPPING_SCHEMA,
    SYSTEM_PROMPT,
    TEMPLATE_SUGGESTION_SCHEMA,
    build_mapping_prompt,
    build_template_suggestion_prompt,
)

LABEL = "Gemini (Google)"
KEY_ENV_VAR = "GEMINI_API_KEY"
MODEL_ENV_VAR = "GEMINI_MODEL"
DEFAULT_MODEL = "gemini-flash-lite-latest"


def _clean_error_message(e: Exception) -> str:
    """google-genai's ClientError/ServerError stringify to a raw JSON error
    body - surface just the human-readable message where we can."""
    if isinstance(e, genai_errors.APIError) and getattr(e, "message", None):
        return e.message
    return str(e)


def _call_structured(system_prompt: str, user_prompt: str, schema: dict, model: str) -> dict:
    api_key = os.environ.get(KEY_ENV_VAR)
    if not api_key:
        raise RuntimeError(f"{KEY_ENV_VAR} is not set. Add a Gemini API key in Settings.")

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_json_schema=schema,
        temperature=0,
    )
    try:
        response = client.models.generate_content(model=model, contents=user_prompt, config=config)
    except Exception as e:
        raise RuntimeError(f"Gemini request failed: {_clean_error_message(e)}") from e

    text = response.text
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini returned output that wasn't valid JSON: {e}") from e


def classify_sheet(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    target_template: TemplateSchema,
    model: str | None = None,
) -> dict:
    model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    user_prompt = build_mapping_prompt(sheet_name, source_columns, sample_rows, target_template)
    return _call_structured(SYSTEM_PROMPT, user_prompt, COLUMN_MAPPING_SCHEMA, model)


def suggest_template(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    candidate_templates: dict[str, TemplateSchema],
    model: str | None = None,
) -> dict:
    model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    user_prompt = build_template_suggestion_prompt(sheet_name, source_columns, sample_rows, candidate_templates)
    return _call_structured(SYSTEM_PROMPT, user_prompt, TEMPLATE_SUGGESTION_SCHEMA, model)


def test_key(api_key: str) -> None:
    """Raise if this key doesn't work. A cheap, non-generating call used to
    validate a key before it's saved to .env."""
    client = genai.Client(api_key=api_key)
    try:
        next(iter(client.models.list()))
    except StopIteration:
        pass  # key works, account just has no visible models - fine
    except Exception as e:
        raise RuntimeError(f"Gemini rejected this key: {_clean_error_message(e)}") from e
