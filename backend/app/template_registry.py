"""
Scans the /templates directory and builds a registry of CyberHQ import
templates: their target field names, inferred descriptions, and any
allowed-value picklists.

Design goal: new templates (e.g. a new framework's maturity assessment,
or a revised structural template) can be dropped into /templates without
touching this code, as long as they follow the naming conventions below.

Naming conventions recognised:
  - upload_<key>_template.csv|xlsx   -> a "structural" template: one row
    per record (risk, issue, vendor, key business system, resource/control).
  - "<Framework Name> Maturity Assessment Template.csv" -> a "framework
    assessment" template: a fixed, ordered question list to be scored
    against. These need row-level (content) matching rather than plain
    column mapping - see services/classifier.py.

Hand-authored field descriptions live in FIELD_NOTES below. These exist
purely to give the LLM classifier better context than a bare header name
- e.g. that "Risk Categories" is a semicolon-delimited list, not free
text. Add to this dict as we learn more about each field; nothing here
is required for a template to be picked up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pandas as pd

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

STRUCTURAL_PATTERN = re.compile(r"^upload_(?P<key>[a-z0-9_]+?)_template", re.IGNORECASE)
FRAMEWORK_PATTERN = re.compile(r"^(?P<framework>.+?)\s+Maturity Assessment Template", re.IGNORECASE)

# Friendly labels for known structural template keys. Anything not listed
# here just falls back to a title-cased version of the key.
KNOWN_LABELS = {
    "risk_register": "Risk Register",
    "issue": "Issue Register",
    "entity": "Entity / Third-Party & Vendor Register",
    "kbs": "Key Business Systems (KBS)",
    "resource": "Resource / Control Library",
}

# key -> {column_name: description}. Optional, additive.
FIELD_NOTES = {
    "risk_register": {
        "Risk ID": "Customer's own reference code for the risk, if they have one. Leave blank if not supplied - do not invent one.",
        "Status": "Lifecycle state, e.g. ACTIVE, CLOSED.",
        "Treatment": "Risk treatment strategy, e.g. MITIGATE, ACCEPT, TRANSFER, AVOID.",
        "Priority": "Overall priority rating, e.g. LOW/MEDIUM/HIGH/CRITICAL.",
        "Inherent Likelihood": "Likelihood before controls, numeric scale (commonly 1-5).",
        "Inherent Impact": "Impact before controls, numeric scale (commonly 1-5).",
        "Residual Likelihood": "Likelihood after controls, numeric scale.",
        "Residual Impact": "Impact after controls, numeric scale.",
        "Financial Impact": "Monetary value, numeric only (no currency symbol).",
        "Due Date": "Date format DD/MM/YYYY.",
        "Owner": "Email address of the risk owner.",
        "Risk Categories": "Semicolon-delimited list of category tags, e.g. 'Technology;Third Party'.",
        "Risk Source": "Where the risk was identified from, e.g. Internal Audit.",
    },
    "issue": {
        "Ref": "Customer's own reference code, if supplied.",
        "Source": "Where the issue came from, e.g. 'Issues Register', 'Audit Findings', 'Pentest Findings', 'SOC Alert'.",
        "Type": "Issue type, e.g. Threat, Vulnerability, Environmental, Technology.",
        "Likelihood": "Text band, e.g. 'Level 1 0% - 10%'.",
        "Likelihood Level": "Numeric level matching the Likelihood band.",
        "Impact Level": "Numeric impact level.",
        "Business Systems": "Semicolon-delimited list of Key Business System codes/names this issue relates to.",
        "Action Name1": "Name of the first remediation action. Repeatable fields (Action Name2, Action Description2, ...) exist for multiple actions per issue.",
        "Action Description1": "Description of the first remediation action.",
        "Action Status1": "Status of the first action, e.g. Initial, Completed.",
        "Action Assignees1": "Semicolon-delimited 'Name,email' pairs assigned to the first action.",
        "Other Fields": "Catch-all free text for anything that doesn't fit a dedicated field, formatted as 'Label:value' pairs separated by ';'.",
        "Data Breach": "Y/N/Yes/No - whether this issue involved a data breach.",
        "System Misuse": "Y/N/Yes/No - whether this issue involved system misuse.",
    },
    "entity": {
        "Type": "Internal or External.",
        "Priority": "Low/Medium/High criticality of this third party/vendor.",
        "Number of Employees": "Banded text, e.g. '< 5k', '5k - 10k', '10k - 50k'.",
        "Data Accessed 1": "First category of data this entity can access.",
        "Data Accessed 2": "Second category of data this entity can access, if applicable.",
        "Stored Data Location 1": "e.g. Onshore/Offshore.",
        "Network Access": "Yes/No.",
        "Physical Access": "Yes/No.",
        "Jurisdiction": "Country/region the entity operates under, if relevant (e.g. for offshore data).",
        "Tags": "Semicolon-delimited free tags.",
    },
    "kbs": {
        "Business owner": "Name or email of the accountable owner.",
        "Status": "e.g. Open, Archived.",
        "Data Stored": "Semicolon-delimited list of data categories stored by this system.",
        "Key processes supported": "Semicolon-delimited list of business processes this system supports.",
        "Applicable regulations": "Semicolon-delimited list, e.g. 'GDPR;CCPA'.",
        "Key": "Yes/No - whether this is a key/critical business system.",
        "Master Control Framework": "Semicolon-delimited list of control framework codes (e.g. NIST 800-53 style 'AU-2(0)-5') this system maps to. This is the crosswalk field.",
    },
    "resource": {
        "Resource Name": "Name of the control, asset, policy, or resource.",
        "Type": "One of: Technology, People, Service Provider, Documentation. See allowed_values.",
        "SubType": "Depends on Type - see allowed_values (e.g. Technology -> 'Network and Infrastructure Security'; People -> 'Full-Time Employee'; Documentation -> 'Policies').",
        "Non-recurring Cost": "One-off cost, numeric only.",
        "Recurring Cost": "Ongoing cost, numeric only.",
        "Cost Frequency": "One of Week/Month/Year - see allowed_values.",
        "Master Control Framework": "Semicolon-delimited list of control framework codes this resource satisfies/maps to. This is the crosswalk field.",
        "Other Fields": "Catch-all free text, e.g. 'System Owner: Name'.",
    },
}


# key -> {field: [allowed values]}. Confirmed with the customer/product owner field by
# field - see PROJECT_BRIEF.md issue #9. Numeric-range fields (Likelihood/Impact) are
# represented as a plain string list too (["1",..,"5"]) so nothing downstream needs a
# separate type for "range" vs "enum".
ALLOWED_VALUES = {
    "risk_register": {
        "Status": ["Active", "Mitigated", "Accepted", "Transferred", "Closed"],
        "Priority": ["Low", "Medium", "High"],
        "Treatment": ["Accept", "Mitigate", "Transfer", "Avoid"],
        "Inherent Likelihood": ["1", "2", "3", "4", "5"],
        "Inherent Impact": ["1", "2", "3", "4", "5"],
        "Residual Likelihood": ["1", "2", "3", "4", "5"],
        "Residual Impact": ["1", "2", "3", "4", "5"],
        "Risk Categories": [
            "Data Breach", "Data Tampering", "Fraud", "Extortion", "Defacement",
            "System Availability", "System Misuse", "Malicious Damage",
        ],
    },
    "issue": {
        "Status": ["Open", "Acknowledged", "In Progress", "Resolved", "Risk Accepted", "Archived"],
        "Type": ["Threat", "Vulnerability", "Environmental", "Technology", "Incident", "Other"],
        "Likelihood Level": ["1", "2", "3", "4", "5"],
        "Impact Level": ["1", "2", "3", "4", "5"],
    },
    "entity": {
        "Industry": [
            "Cloud Infrastructure - SaaS, Paas or IaaS", "Construction & Property",
            "Energy & Utilities", "Fast Moving Consumer Goods", "Financial Services",
            "Government", "Industry Association", "Mining & Metals", "Other", "Technology",
            "Telecommunications", "Transportation",
        ],
        "Priority": ["High", "Medium", "Low"],
        "Type": ["Internal", "External"],
        "Network Access": ["Yes", "No"],
        "Physical Access": ["Yes", "No"],
        # As given, verbatim - includes overlapping bands (e.g. "> 5k" and "< 100k" both
        # present). Not reordered/deduped - this is the customer's own configured list.
        "Number of Employees": [
            "< 100k", "10k - 50k", "50k - 100k", "50 - 100", "20 - 50", "> 5k", "> 500",
            "100 - 500", "5k - 10k", "<20",
        ],
    },
    "kbs": {
        "Status": ["Open", "Archived"],
        "Key": ["Yes", "No"],
        "Data Stored": [
            "Intellectual Property, Trade Secrets & Strategy",
            "Personally Identifiable Information (PII)",
            "Non-PII Customer Information",
            "Non-PII Employee & HR Information",
            "Marketing and Communications",
            "Legal, Contracts and Agreements",
            "Project Documentation (Sensitive)",
            "Project Documentation (Non-sensitive)",
            "Software Codes, Libraries and Technical Repos",
            "Master Keys, Logs and Configuration",
            "Other",
        ],
    },
}

# key -> [fields] whose values are ';'-delimited multi-select (a single cell can hold
# several values). Needed as data, not just the prose already in FIELD_NOTES, since both
# the crosswalk endpoint and exporter.py need to split/rejoin on it.
DELIMITED_FIELDS = {
    "risk_register": ["Risk Categories"],
    "kbs": ["Data Stored"],
}

# key -> [fields] that get a per-row content-based recommendation (from CONTENT_FIELDS
# below) instead of a plain default, when no source column maps to them at all.
CONTENT_RECOMMEND_FIELDS = {
    "risk_register": ["Risk Categories"],
    "issue": ["Type"],
}

# key -> [target fields] whose mapped source column(s) supply the per-row text used for
# content-based recommendation - e.g. a risk's Name + Description.
CONTENT_FIELDS = {
    "risk_register": ["Name", "Description"],
    "issue": ["Title", "Description"],
}


@dataclass
class TemplateSchema:
    key: str
    label: str
    kind: str  # "structural" or "framework_assessment"
    source_file: str
    columns: list[str]
    field_notes: dict = field(default_factory=dict)
    allowed_values: dict = field(default_factory=dict)  # column -> [values]
    delimited_fields: list = field(default_factory=list)
    content_recommend_fields: list = field(default_factory=list)
    sample_rows: list[dict] = field(default_factory=list)


def _read_csv_headers_and_samples(path: Path, n_samples: int = 3) -> tuple[list[str], list[dict]]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, nrows=n_samples + 1)
    columns = list(df.columns)
    samples = df.head(n_samples).to_dict(orient="records")
    return columns, samples


def _read_xlsx_main_sheet(path: Path, n_samples: int = 3):
    wb = openpyxl.load_workbook(path, data_only=True)
    # Prefer a sheet that isn't an obvious reference/lookup sheet.
    data_sheet = None
    prefill_sheet = None
    for ws in wb.worksheets:
        if ws.title.strip().lower() in ("prefill", "lookup", "lookups", "reference", "dropdowns"):
            prefill_sheet = ws
        elif data_sheet is None:
            data_sheet = ws
    if data_sheet is None:
        data_sheet = wb.worksheets[0]

    rows = list(data_sheet.iter_rows(values_only=True))
    header_row = rows[0] if rows else []
    columns = [str(c) for c in header_row if c is not None]
    ncols = len(columns)

    samples = []
    for row in rows[1 : 1 + n_samples]:
        row = row[:ncols]
        samples.append({columns[i]: row[i] for i in range(len(row))})

    allowed_values: dict[str, list[str]] = {}
    if prefill_sheet is not None:
        prefill_rows = list(prefill_sheet.iter_rows(values_only=True))
        if prefill_rows:
            headers = prefill_rows[0]
            for col_idx, header in enumerate(headers):
                if header is None:
                    continue
                values = [
                    r[col_idx]
                    for r in prefill_rows[1:]
                    if col_idx < len(r) and r[col_idx] not in (None, "")
                ]
                if values:
                    allowed_values[str(header)] = [str(v) for v in values]

    return columns, samples, allowed_values


def load_registry() -> dict[str, TemplateSchema]:
    """Scan TEMPLATES_DIR and return {template_key: TemplateSchema}."""
    registry: dict[str, TemplateSchema] = {}

    if not TEMPLATES_DIR.exists():
        return registry

    for path in sorted(TEMPLATES_DIR.iterdir()):
        if path.name.startswith(".") or path.is_dir():
            continue

        structural_match = STRUCTURAL_PATTERN.match(path.stem)
        framework_match = FRAMEWORK_PATTERN.match(path.stem)

        if structural_match:
            key = structural_match.group("key").lower()
            label = KNOWN_LABELS.get(key, key.replace("_", " ").title())
            allowed_values = {}
            if path.suffix.lower() == ".xlsx":
                columns, samples, allowed_values = _read_xlsx_main_sheet(path)
            else:
                columns, samples = _read_csv_headers_and_samples(path)

            # Hardcoded, confirmed values are additive with whatever a Prefill sheet
            # already supplied (Prefill wins on the rare chance a field appears in both).
            allowed_values = {**ALLOWED_VALUES.get(key, {}), **allowed_values}

            registry[key] = TemplateSchema(
                key=key,
                label=label,
                kind="structural",
                source_file=path.name,
                columns=columns,
                field_notes=FIELD_NOTES.get(key, {}),
                allowed_values=allowed_values,
                delimited_fields=DELIMITED_FIELDS.get(key, []),
                content_recommend_fields=CONTENT_RECOMMEND_FIELDS.get(key, []),
                sample_rows=samples,
            )

        elif framework_match:
            framework = framework_match.group("framework").strip()
            key = "assessment_" + re.sub(r"[^a-z0-9]+", "_", framework.lower()).strip("_")
            columns, samples = _read_csv_headers_and_samples(path, n_samples=5)
            registry[key] = TemplateSchema(
                key=key,
                label=f"{framework} Maturity Assessment",
                kind="framework_assessment",
                source_file=path.name,
                columns=columns,
                field_notes={
                    "Index": "Fixed question code in this framework, e.g. 'GV.OC-01'. Do not invent - only fill Current Score/Current Answer/Notes for existing rows.",
                    "Applicable": "Yes/No.",
                    "Current Score": "Numeric score against this question.",
                    "Current Answer": "Text/percentage answer for this question.",
                    "Notes": "Free text notes.",
                },
                sample_rows=samples,
            )
        # else: unrecognised filename - ignored for now. Logged by caller if needed.

    return registry


def structural_templates(registry: dict[str, TemplateSchema]) -> dict[str, TemplateSchema]:
    return {k: v for k, v in registry.items() if v.kind == "structural"}


def framework_assessment_templates(registry: dict[str, TemplateSchema]) -> dict[str, TemplateSchema]:
    return {k: v for k, v in registry.items() if v.kind == "framework_assessment"}
