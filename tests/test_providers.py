"""Unit tests for the provider layer (Claude + Gemini), with the actual
SDK clients mocked - no real API calls, no API key needed to run these.
Confirms the prompt-building/response-parsing plumbing works; the real
network calls are verified manually against live APIs (see README and
PROJECT_BRIEF.md for that history), not here."""

from __future__ import annotations

import json

import pytest

from app.services import classifier as classifier_service
from app.services.providers import anthropic_provider, gemini_provider
from app.template_registry import TemplateSchema


@pytest.fixture
def target_template():
    return TemplateSchema(
        key="issue",
        label="Issue Register",
        kind="structural",
        source_file="x.csv",
        columns=["Title", "Status"],
        allowed_values={"Status": ["Open", "Closed"]},
    )


def _mock_anthropic_tool_response(mocker, tool_name: str, payload: dict):
    """Patches anthropic_provider.Anthropic so .messages.create(...)
    returns a canned tool_use block, as if the model called the tool."""
    # NB: Mock(name=...) sets the mock's own debug repr, NOT a `.name`
    # attribute - has to be assigned separately to actually stub `.name`.
    block = mocker.Mock(type="tool_use", input=payload)
    block.name = tool_name
    response = mocker.Mock(content=[block])
    client = mocker.Mock()
    client.messages.create.return_value = response
    mocker.patch.object(anthropic_provider, "Anthropic", return_value=client)
    return client


def _mock_gemini_response(mocker, payload: dict):
    """Patches gemini_provider.genai.Client so .models.generate_content(...)
    returns a canned JSON text response."""
    response = mocker.Mock(text=json.dumps(payload))
    client = mocker.Mock()
    client.models.generate_content.return_value = response
    mocker.patch.object(gemini_provider.genai, "Client", return_value=client)
    return client


class TestAnthropicProvider:
    def test_classify_sheet_returns_tool_input(self, mocker, monkeypatch, target_template):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        payload = {"column_mappings": [{"source_column": "A", "target_field": "Title", "confidence": 0.9, "rationale": "x"}]}
        _mock_anthropic_tool_response(mocker, "submit_mapping", payload)

        result = anthropic_provider.classify_sheet("Sheet1", ["A"], [{"A": "hi"}], target_template)
        assert result == payload

    def test_match_column_values_returns_tool_input(self, mocker, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        payload = {"matches": [{"candidate_index": 0, "best_match": "Open", "confidence": 0.8, "rationale": "x"}]}
        _mock_anthropic_tool_response(mocker, "submit_value_matches", payload)

        result = anthropic_provider.match_column_values("Status", ["Open", "Closed"], ["open"])
        assert result == payload

    def test_missing_api_key_raises_before_any_client_call(self, monkeypatch, target_template):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            anthropic_provider.classify_sheet("Sheet1", ["A"], [], target_template)

    def test_wrong_tool_returned_raises(self, mocker, monkeypatch, target_template):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        _mock_anthropic_tool_response(mocker, "some_other_tool", {})
        with pytest.raises(RuntimeError, match="did not return"):
            anthropic_provider.classify_sheet("Sheet1", ["A"], [], target_template)


class TestGeminiProvider:
    def test_classify_sheet_returns_parsed_json(self, mocker, monkeypatch, target_template):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        payload = {"column_mappings": [{"source_column": "A", "target_field": "Title", "confidence": 0.9, "rationale": "x"}]}
        _mock_gemini_response(mocker, payload)

        result = gemini_provider.classify_sheet("Sheet1", ["A"], [{"A": "hi"}], target_template)
        assert result == payload

    def test_recommend_from_content_returns_parsed_json(self, mocker, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        payload = {"matches": [{"row_index": 0, "best_match": "Extortion", "confidence": 0.95, "rationale": "x"}]}
        _mock_gemini_response(mocker, payload)

        result = gemini_provider.recommend_from_content("Risk Categories", ["Extortion", "Fraud"], ["a ransomware row"])
        assert result == payload

    def test_missing_api_key_raises_before_any_client_call(self, monkeypatch, target_template):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            gemini_provider.classify_sheet("Sheet1", ["A"], [], target_template)

    def test_non_json_response_raises_a_clean_error(self, mocker, monkeypatch, target_template):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        response = mocker.Mock(text="not json at all")
        client = mocker.Mock()
        client.models.generate_content.return_value = response
        mocker.patch.object(gemini_provider.genai, "Client", return_value=client)

        with pytest.raises(RuntimeError, match="valid JSON"):
            gemini_provider.classify_sheet("Sheet1", ["A"], [], target_template)


class TestClassifierDispatch:
    def test_explicit_llm_provider_env_wins(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")  # both keyed - explicit still wins
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        assert classifier_service.get_active_provider_name() == "gemini"

    def test_falls_back_to_the_one_provider_with_a_key(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        assert classifier_service.get_active_provider_name() == "gemini"

    def test_falls_back_to_default_when_nothing_is_configured(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert classifier_service.get_active_provider_name() == classifier_service.DEFAULT_PROVIDER

    def test_unknown_provider_name_raises(self, target_template):
        with pytest.raises(RuntimeError, match="Unknown LLM provider"):
            classifier_service.classify_sheet("Sheet1", ["A"], [], target_template, provider="not-a-real-provider")
