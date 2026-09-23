"""
Calls an LLM (Claude or Gemini, whichever is configured in Settings) to
propose a column-to-field mapping - with a confidence score and a
plain-English rationale for each column - once the reviewer has picked
which CyberHQ template a source sheet should go into. Also offers an
optional, opt-in template suggestion for when the reviewer isn't sure
which template fits (see suggest_template below).

This module never touches the actual source data values - it only ever
sees column headers and a handful of sample rows, and only ever proposes
where a whole column should go. Nothing here writes the final CSV; that
happens in exporter.py, only after a human has approved (and can edit)
what this module proposes. See README "How data integrity is preserved".

The actual prompt and per-provider API calls live under services/providers/
- this module just picks which one to use based on the LLM_PROVIDER setting.
"""

from __future__ import annotations

import os

from app.template_registry import TemplateSchema

from .providers import anthropic_provider, gemini_provider

PROVIDERS = {
    "anthropic": anthropic_provider,
    "gemini": gemini_provider,
}

DEFAULT_PROVIDER = "anthropic"


def get_active_provider_name() -> str:
    configured = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if configured:
        return configured
    # Nothing explicitly chosen yet - if exactly one provider has a key
    # sitting in the environment, use that rather than forcing a default
    # that has no key either.
    keyed = [name for name, mod in PROVIDERS.items() if os.environ.get(mod.KEY_ENV_VAR)]
    if len(keyed) == 1:
        return keyed[0]
    return DEFAULT_PROVIDER


def _get_module(provider: str | None):
    provider_name = (provider or get_active_provider_name()).strip().lower()
    module = PROVIDERS.get(provider_name)
    if module is None:
        raise RuntimeError(
            f"Unknown LLM provider '{provider_name}'. Choose one of: {', '.join(PROVIDERS)}."
        )
    return module


def classify_sheet(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    target_template: TemplateSchema,
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    """Ask the configured LLM provider to map one source sheet's columns
    into the given (already-chosen) target template."""

    module = _get_module(provider)
    return module.classify_sheet(
        sheet_name=sheet_name,
        source_columns=source_columns,
        sample_rows=sample_rows,
        target_template=target_template,
        model=model,
    )


def suggest_template(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    candidate_templates: dict[str, TemplateSchema],
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    """Ask the configured LLM provider which candidate template best fits
    this sheet. Opt-in helper only - not used on the default mapping path,
    since it has to send every candidate template's fields in one prompt."""

    module = _get_module(provider)
    return module.suggest_template(
        sheet_name=sheet_name,
        source_columns=source_columns,
        sample_rows=sample_rows,
        candidate_templates=candidate_templates,
        model=model,
    )


def match_column_values(
    field_name: str,
    allowed_values: list[str],
    candidates: list[str],
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    """Ask the configured LLM provider to translate each distinct value found
    in a mapped source column into the closest allowed value for a
    constrained target field (e.g. Status). See issue #9."""

    module = _get_module(provider)
    return module.match_column_values(
        field_name=field_name,
        allowed_values=allowed_values,
        candidates=candidates,
        model=model,
    )


def recommend_from_content(
    field_name: str,
    allowed_values: list[str],
    row_contents: list[str],
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    """Ask the configured LLM provider to recommend an allowed value per row,
    from that row's own content - used only when no source column maps to a
    constrained target field at all (e.g. Risk Categories, Issue Type)."""

    module = _get_module(provider)
    return module.recommend_from_content(
        field_name=field_name,
        allowed_values=allowed_values,
        row_contents=row_contents,
        model=model,
    )
