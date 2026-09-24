"""API-level tests against the real FastAPI app (TestClient), exercising
the actual endpoint chain a browser session goes through. All LLM calls
are mocked at the app.services.classifier boundary - no real API calls,
no API key needed to run these."""

from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _upload(client, filename: str):
    with (FIXTURES_DIR / filename).open("rb") as f:
        res = client.post("/api/upload", files={"file": (filename, f, "text/csv")})
    assert res.status_code == 200, res.text
    return res.json()


class TestHealthAndTemplates:
    def test_health(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.json()["app"] == "grc-migration-tool"

    def test_templates_lists_all_structural_templates(self, client):
        res = client.get("/api/templates")
        assert res.status_code == 200
        keys = {t["key"] for t in res.json()["structural"]}
        assert keys == {"risk_register", "issue", "entity", "kbs", "resource"}


class TestUploadAndHeaderConfirmation:
    def test_upload_headers_first_csv(self, client):
        data = _upload(client, "headers_first.csv")
        assert len(data["sheets"]) == 1
        sheet = data["sheets"][0]
        assert sheet["guessed_header_row"] == 1
        assert sheet["row_count"] == 3  # header + 2 data rows

    def test_upload_title_row_then_header_csv_guesses_row_three(self, client):
        data = _upload(client, "title_row_then_header.csv")
        assert data["sheets"][0]["guessed_header_row"] == 3

    def test_set_header_returns_real_columns(self, client):
        data = _upload(client, "title_row_then_header.csv")
        session_id, sheet_name = data["session_id"], data["sheets"][0]["name"]
        res = client.post(f"/api/sessions/{session_id}/set-header", json={"sheet_name": sheet_name, "header_row": 3})
        assert res.status_code == 200
        assert res.json()["columns"] == ["Ref", "Title", "Description", "Status"]
        assert res.json()["row_count"] == 2

    def test_classify_before_header_confirmed_is_a_clean_400_not_404(self, client):
        data = _upload(client, "headers_first.csv")
        session_id, sheet_name = data["session_id"], data["sheets"][0]["name"]
        res = client.post(f"/api/sessions/{session_id}/classify", json={"sheet_name": sheet_name, "template_key": "issue"})
        assert res.status_code == 400
        assert "header row" in res.json()["detail"].lower()

    def test_unknown_session_is_404(self, client):
        res = client.post("/api/sessions/does-not-exist/set-header", json={"sheet_name": "x", "header_row": 1})
        assert res.status_code == 404


class TestFullChain:
    def test_upload_to_export(self, client, mocker):
        mocker.patch(
            "app.services.classifier.classify_sheet",
            return_value={
                "column_mappings": [
                    {"source_column": "Ref", "target_field": None, "confidence": 0, "rationale": "id, no target"},
                    {"source_column": "Title", "target_field": "Title", "confidence": 0.95, "rationale": "x"},
                    {"source_column": "Description", "target_field": "Description", "confidence": 0.9, "rationale": "x"},
                    {"source_column": "Status", "target_field": "Status", "confidence": 0.9, "rationale": "x"},
                ]
            },
        )

        data = _upload(client, "headers_first.csv")
        session_id, sheet_name = data["session_id"], data["sheets"][0]["name"]

        header_res = client.post(f"/api/sessions/{session_id}/set-header", json={"sheet_name": sheet_name, "header_row": 1})
        assert header_res.status_code == 200

        classify_res = client.post(
            f"/api/sessions/{session_id}/classify", json={"sheet_name": sheet_name, "template_key": "issue"}
        )
        assert classify_res.status_code == 200
        assert len(classify_res.json()["column_mappings"]) == 4

        export_res = client.post(
            f"/api/sessions/{session_id}/export",
            json={
                "sheet_name": sheet_name,
                "template_key": "issue",
                "mapping": [
                    {"source_column": "Ref", "target_field": None},
                    {"source_column": "Title", "target_field": "Title"},
                    {"source_column": "Description", "target_field": "Description"},
                    {"source_column": "Status", "target_field": "Status"},
                ],
                "confirmed_drop_columns": ["Ref"],
            },
        )
        assert export_res.status_code == 200
        assert "First synthetic issue" in export_res.text
        # "Ref" was confirmed dropped, not mapped - that's one expected warning.
        assert export_res.headers["x-export-warnings"] == "1"

    def test_export_with_unresolved_columns_is_409_not_a_silent_drop(self, client):
        data = _upload(client, "headers_first.csv")
        session_id, sheet_name = data["session_id"], data["sheets"][0]["name"]
        client.post(f"/api/sessions/{session_id}/set-header", json={"sheet_name": sheet_name, "header_row": 1})

        res = client.post(
            f"/api/sessions/{session_id}/export",
            json={
                "sheet_name": sheet_name,
                "template_key": "issue",
                "mapping": [
                    {"source_column": "Ref", "target_field": None},
                    {"source_column": "Title", "target_field": "Title"},
                    {"source_column": "Description", "target_field": None},
                    {"source_column": "Status", "target_field": None},
                ],
                # Ref/Description/Status left unmapped and NOT confirmed dropped.
            },
        )
        assert res.status_code == 409
        assert set(res.json()["detail"]["columns"]) == {"Ref", "Description", "Status"}

    def test_export_with_value_map_translates_values(self, client):
        data = _upload(client, "headers_first.csv")  # Status values: Open, Acknowledged
        session_id, sheet_name = data["session_id"], data["sheets"][0]["name"]
        client.post(f"/api/sessions/{session_id}/set-header", json={"sheet_name": sheet_name, "header_row": 1})

        res = client.post(
            f"/api/sessions/{session_id}/export",
            json={
                "sheet_name": sheet_name,
                "template_key": "issue",
                "mapping": [
                    {"source_column": "Ref", "target_field": None},
                    {"source_column": "Title", "target_field": "Title"},
                    {"source_column": "Description", "target_field": None},
                    {"source_column": "Status", "target_field": "Status", "value_map": {"Acknowledged": "Resolved"}},
                ],
                "confirmed_drop_columns": ["Ref", "Description"],
            },
        )
        assert res.status_code == 200
        lines = res.text.strip().splitlines()
        assert "Resolved" in lines[2]  # second data row had "Acknowledged"
        assert "Open" in lines[1]  # first data row's "Open" passes through untouched


class TestConstrainedValueEndpoints:
    def test_crosswalk_values_returns_matches_keyed_by_source_value(self, client, mocker):
        # headers_first.csv's Status column has "Open" and "Acknowledged" -
        # the endpoint sorts unique candidates alphabetically before sending
        # them to the LLM, so index 0 = "Acknowledged", index 1 = "Open".
        mocker.patch(
            "app.services.classifier.match_column_values",
            return_value={
                "matches": [
                    {"candidate_index": 0, "best_match": "Acknowledged", "confidence": 1.0, "rationale": "exact match"},
                    {"candidate_index": 1, "best_match": "Open", "confidence": 1.0, "rationale": "exact match"},
                ]
            },
        )
        data = _upload(client, "headers_first.csv")
        session_id, sheet_name = data["session_id"], data["sheets"][0]["name"]
        client.post(f"/api/sessions/{session_id}/set-header", json={"sheet_name": sheet_name, "header_row": 1})

        res = client.post(
            f"/api/sessions/{session_id}/crosswalk-values",
            json={"sheet_name": sheet_name, "source_column": "Status", "target_field": "Status", "template_key": "issue"},
        )
        assert res.status_code == 200
        matches = res.json()["matches"]
        assert {m["source_value"] for m in matches} == {"Open", "Acknowledged"}

    def test_crosswalk_values_for_field_with_no_allowed_values_is_400(self, client):
        data = _upload(client, "headers_first.csv")
        session_id, sheet_name = data["session_id"], data["sheets"][0]["name"]
        client.post(f"/api/sessions/{session_id}/set-header", json={"sheet_name": sheet_name, "header_row": 1})

        res = client.post(
            f"/api/sessions/{session_id}/crosswalk-values",
            json={"sheet_name": sheet_name, "source_column": "Title", "target_field": "Title", "template_key": "issue"},
        )
        assert res.status_code == 400

    def test_recommend_from_content_aligns_recommendations_to_row_order(self, client, mocker):
        mocker.patch(
            "app.services.classifier.recommend_from_content",
            return_value={
                "matches": [
                    {"row_index": 0, "best_match": "Threat", "confidence": 0.7, "rationale": "x"},
                    {"row_index": 1, "best_match": "Vulnerability", "confidence": 0.6, "rationale": "y"},
                ]
            },
        )
        data = _upload(client, "headers_first.csv")
        session_id, sheet_name = data["session_id"], data["sheets"][0]["name"]
        client.post(f"/api/sessions/{session_id}/set-header", json={"sheet_name": sheet_name, "header_row": 1})

        res = client.post(
            f"/api/sessions/{session_id}/recommend-from-content",
            json={
                "sheet_name": sheet_name,
                "target_field": "Type",
                "template_key": "issue",
                "mapping": [
                    {"source_column": "Title", "target_field": "Title"},
                    {"source_column": "Description", "target_field": "Description"},
                ],
            },
        )
        assert res.status_code == 200
        recs = res.json()["recommendations"]
        assert len(recs) == 2
        assert recs[0]["suggested_target"] == "Threat"
        assert recs[1]["suggested_target"] == "Vulnerability"
