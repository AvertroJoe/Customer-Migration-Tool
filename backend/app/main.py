from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ingestion, state
from app.services import classifier as classifier_service
from app.services import exporter as exporter_service
from app.template_registry import (
    TemplateSchema,
    framework_assessment_templates,
    load_registry,
    structural_templates,
)

# Load .env if python-dotenv-style file exists (kept dependency-free: tiny manual loader)
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

REGISTRY = load_registry()

app = FastAPI(title="CyberHQ GRC Data Migration Tool")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Templates ----------

@app.get("/api/templates")
def list_templates():
    def summarise(t: TemplateSchema):
        return {
            "key": t.key,
            "label": t.label,
            "kind": t.kind,
            "source_file": t.source_file,
            "columns": t.columns,
            "field_notes": t.field_notes,
            "allowed_values": t.allowed_values,
        }

    return {
        "structural": [summarise(t) for t in structural_templates(REGISTRY).values()],
        "framework_assessment": [summarise(t) for t in framework_assessment_templates(REGISTRY).values()],
    }


# ---------- Upload ----------

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xlsm"):
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Upload a .csv or .xlsx file.")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        sheets = ingestion.read_sheets(tmp_path)
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)

    if not sheets:
        raise HTTPException(400, "No non-empty sheets found in this file.")

    session = state.create_session(source_filename=file.filename, sheets=sheets)

    sheet_summaries = []
    for name, df in sheets.items():
        sheet_summaries.append(
            {
                "name": name,
                "columns": list(df.columns),
                "row_count": len(df),
                "sample_rows": df.head(5).to_dict(orient="records"),
            }
        )

    return {"session_id": session.id, "source_filename": file.filename, "sheets": sheet_summaries}


# ---------- Classification ----------

class ClassifyRequest(BaseModel):
    sheet_name: str
    template_key: str | None = None  # force classification against one specific template


@app.post("/api/sessions/{session_id}/classify")
def classify(session_id: str, req: ClassifyRequest):
    session = state.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired.")
    if req.sheet_name not in session.sheets:
        raise HTTPException(404, f"Sheet '{req.sheet_name}' not found in this session.")

    df = session.sheets[req.sheet_name]
    candidates = structural_templates(REGISTRY)
    if req.template_key:
        if req.template_key not in candidates:
            raise HTTPException(400, f"Unknown template_key '{req.template_key}'")
        candidates = {req.template_key: candidates[req.template_key]}

    try:
        result = classifier_service.classify_sheet(
            sheet_name=req.sheet_name,
            source_columns=list(df.columns),
            sample_rows=df.head(5).to_dict(orient="records"),
            candidate_templates=candidates,
        )
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    target = REGISTRY.get(result.get("best_template", ""))
    result["target_template"] = (
        {
            "key": target.key,
            "label": target.label,
            "columns": target.columns,
            "field_notes": target.field_notes,
            "allowed_values": target.allowed_values,
        }
        if target
        else None
    )
    return result


# ---------- Export ----------

class MappingEntry(BaseModel):
    source_column: str
    target_field: str | None = None
    transform: str = "none"


class ExportRequest(BaseModel):
    sheet_name: str
    template_key: str
    mapping: list[MappingEntry]
    catch_all_field: str | None = None
    confirmed_drop_columns: list[str] = []


@app.post("/api/sessions/{session_id}/export")
def export(session_id: str, req: ExportRequest):
    session = state.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired.")
    if req.sheet_name not in session.sheets:
        raise HTTPException(404, f"Sheet '{req.sheet_name}' not found in this session.")
    target = REGISTRY.get(req.template_key)
    if not target:
        raise HTTPException(400, f"Unknown template_key '{req.template_key}'")

    df = session.sheets[req.sheet_name]
    mapping = [
        exporter_service.MappingRow(m.source_column, m.target_field, m.transform) for m in req.mapping
    ]

    try:
        output_df, warnings = exporter_service.build_export(
            source_df=df,
            mapping=mapping,
            target=target,
            catch_all_field=req.catch_all_field,
            confirmed_drop_columns=req.confirmed_drop_columns,
        )
    except exporter_service.UnresolvedColumnsError as e:
        raise HTTPException(
            409,
            {
                "error": "unresolved_columns",
                "message": str(e),
                "columns": e.columns,
            },
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    buf = io.StringIO()
    output_df.to_csv(buf, index=False)
    buf.seek(0)

    out_filename = f"{target.key}_import_{session.source_filename.rsplit('.', 1)[0]}.csv"
    response = StreamingResponse(iter([buf.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{out_filename}"'
    response.headers["X-Export-Warnings"] = str(len(warnings))
    return response


# ---------- Frontend ----------

_frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend" / "static"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
