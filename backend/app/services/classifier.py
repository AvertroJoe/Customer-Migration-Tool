"""
Calls an LLM (Claude or Gemini, whichever is configured in Settings) to
(1) work out which CyberHQ structural template a source sheet most
resembles, and (2) propose a column-to-field mapping with a confidence
score and a plain-English rationale for each column.

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


def classify_sheet(
    sheet_name: str,
    source_columns: list[str],
    sample_rows: list[dict],
    candidate_templates: dict[str, TemplateSchema],
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    """Ask the configured LLM provider to classify one source sheet against
    the candidate structural templates."""

    provider_name = (provider or get_active_provider_name()).strip().lower()
    module = PROVIDERS.get(provider_name)
    if module is None:
        raise RuntimeError(
            f"Unknown LLM provider '{provider_name}'. Choose one of: {', '.join(PROVIDERS)}."
        )

    return module.classify_sheet(
        sheet_name=sheet_name,
        source_columns=source_columns,
        sample_rows=sample_rows,
        candidate_templates=candidate_templates,
        model=model,
    )
