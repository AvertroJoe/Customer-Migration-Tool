"""Shared pytest fixtures. See pytest.ini for how `backend/` ends up on
sys.path so `from app import ...` resolves the same way it does at
runtime (mirrors `uvicorn app.main:app --app-dir backend`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def upload_fixture(client):
    """Uploads a fixture file and returns its session_id + parsed response."""

    def _upload(filename: str):
        path = FIXTURES_DIR / filename
        with path.open("rb") as f:
            res = client.post("/api/upload", files={"file": (filename, f, "text/csv")})
        assert res.status_code == 200, res.text
        return res.json()

    return _upload
