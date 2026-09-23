from __future__ import annotations

import io
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ingestion, settings, state
from app.services import classifier as classifier_service
from app.services import exporter as exporter_service
from app.template_registry import (
    TemplateSchema,
    framework_assessment_templates,
    load_registry,
    structural_templates,
)

settings.load_dotenv_into_environ()

REGISTRY = load_registry()

app = FastAPI(title="CyberHQ GRC Data Migration Tool")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Health ----------

@app.get("/api/health")
def health():
    """Used by launcher/bootstrap.py to check whether an instance is
    already running on this port before starting another one."""
    return {"app": "grc-migration-tool", "status": "ok"}


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


# ---------- Settings (LLM provider + API key) ----------

@app.get("/api/settings")
def get_settings():
    return settings.get_status()


class SettingsUpdate(BaseModel):
    provider: str
    api_key: str | None = None  # omit/blank to keep whatever key is already saved
    model: str | None = None


@app.post("/api/settings")
def update_settings(req: SettingsUpdate):
    provider = req.provider.strip().lower()
    if provider not in settings.PROVIDER_INFO:
        raise HTTPException(
            400, f"Unknown provider '{provider}'. Choose one of: {', '.join(settings.PROVIDER_INFO)}."
        )
    info = settings.PROVIDER_INFO[provider]
    current = settings.get_status()
    already_has_key = next(p for p in current["providers"] if p["provider"] == provider)["key_set"]

    api_key = (req.api_key or "").strip()
    if not api_key and not already_has_key:
        raise HTTPException(400, f"Enter a {info['label']} API key to enable this provider.")

    updates = {"LLM_PROVIDER": provider}
    if api_key:
        # Validate before persisting, so a typo or a revoked key doesn't
        # silently get saved and only surface as a failure later on.
        try:
            classifier_service.PROVIDERS[provider].test_key(api_key)
        except Exception as e:
            raise HTTPException(400, f"Could not verify this key with {info['label']}: {e}")
        updates[info["key_env_var"]] = api_key
    if req.model:
        updates[info["model_env_var"]] = req.model.strip()

    settings.write_env_values(updates)
    return settings.get_status()


class SettingsTestRequest(BaseModel):
    provider: str
    api_key: str


@app.post("/api/settings/test")
def test_settings(req: SettingsTestRequest):
    provider = req.provider.strip().lower()
    if provider not in settings.PROVIDER_INFO:
        raise HTTPException(400, f"Unknown provider '{provider}'.")
    api_key = req.api_key.strip()
    if not api_key:
        raise HTTPException(400, "Enter an API key to test.")
    try:
        classifier_service.PROVIDERS[provider].test_key(api_key)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


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
        raw_sheets = ingestion.read_raw_sheets(tmp_path)
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)

    if not raw_sheets:
        raise HTTPException(400, "No non-empty sheets found in this file.")

    session = state.create_session(source_filename=file.filename, raw_sheets=raw_sheets)

    sheet_summaries = []
    for name, rows in raw_sheets.items():
        sheet_summaries.append(
            {
                "name": name,
                "row_count": len(rows),
                "col_count": max((len(r) for r in rows), default=0),
                # 1-indexed to match how the reviewer sees row numbers in
                # their own spreadsheet - confirmed via /set-header below.
                "guessed_header_row": ingestion.guess_header_row(rows) + 1,
                "preview_rows": rows[:12],
            }
        )

    return {"session_id": session.id, "source_filename": file.filename, "sheets": sheet_summaries}


class SetHeaderRequest(BaseModel):
    sheet_name: str
    header_row: int  # 1-indexed, as shown to the reviewer in the preview


@app.post("/api/sessions/{session_id}/set-header")
def set_header(session_id: str, req: SetHeaderRequest):
    """Confirms which raw row is the real header for a sheet, turning it
    into the DataFrame classify/export operate on. See ingestion.py -
    nothing guesses this without the reviewer confirming it here first."""
    session = state.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired.")
    if req.sheet_name not in session.raw_sheets:
        raise HTTPException(404, f"Sheet '{req.sheet_name}' not found in this session.")

    rows = session.raw_sheets[req.sheet_name]
    try:
        df = ingestion.build_dataframe(rows, req.header_row - 1)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if df.empty:
        raise HTTPException(400, "No data rows found beneath that header row - pick a different row.")

    session.sheets[req.sheet_name] = df
    return {
        "columns": list(df.columns),
        "row_count": len(df),
        "sample_rows": df.head(5).to_dict(orient="records"),
    }


def _get_confirmed_sheet(session: state.Session, sheet_name: str):
    """Look up a sheet's DataFrame, distinguishing "no such sheet" from
    "this sheet exists but the header row hasn't been confirmed yet" -
    classify/suggest-template/export all need exactly this check."""
    if sheet_name not in session.raw_sheets:
        raise HTTPException(404, f"Sheet '{sheet_name}' not found in this session.")
    if sheet_name not in session.sheets:
        raise HTTPException(400, f"Confirm the header row for sheet '{sheet_name}' first (see /set-header).")
    return session.sheets[sheet_name]


# ---------- Classification ----------

class ClassifyRequest(BaseModel):
    sheet_name: str
    template_key: str  # the reviewer has already chosen which template to map into


@app.post("/api/sessions/{session_id}/classify")
def classify(session_id: str, req: ClassifyRequest):
    session = state.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired.")
    df = _get_confirmed_sheet(session, req.sheet_name)

    candidates = structural_templates(REGISTRY)
    if req.template_key not in candidates:
        raise HTTPException(400, f"Unknown template_key '{req.template_key}'")
    target = candidates[req.template_key]

    try:
        result = classifier_service.classify_sheet(
            sheet_name=req.sheet_name,
            source_columns=list(df.columns),
            sample_rows=df.head(5).to_dict(orient="records"),
            target_template=target,
        )
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    return {"column_mappings": result.get("column_mappings", [])}


class SuggestTemplateRequest(BaseModel):
    sheet_name: str


@app.post("/api/sessions/{session_id}/suggest-template")
def suggest_template(session_id: str, req: SuggestTemplateRequest):
    """Opt-in helper for when the reviewer isn't sure which CyberHQ template
    fits - separate from /classify so the normal mapping path never has to
    send every candidate template's fields in one prompt (see
    services/classifier.py's module docstring)."""
    session = state.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired.")
    df = _get_confirmed_sheet(session, req.sheet_name)
    candidates = structural_templates(REGISTRY)

    try:
        result = classifier_service.suggest_template(
            sheet_name=req.sheet_name,
            source_columns=list(df.columns),
            sample_rows=df.head(5).to_dict(orient="records"),
            candidate_templates=candidates,
        )
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    target = REGISTRY.get(result.get("best_template", ""))
    result["target_template"] = (
        {"key": target.key, "label": target.label} if target else None
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
    df = _get_confirmed_sheet(session, req.sheet_name)
    target = REGISTRY.get(req.template_key)
    if not target:
        raise HTTPException(400, f"Unknown template_key '{req.template_key}'")

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
