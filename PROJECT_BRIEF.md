# CyberHQ GRC Data Migration Tool — Project Brief

## Goal

Build a tool that takes a customer's existing GRC spreadsheets (risk registers, issue logs,
vendor/third-party lists, control libraries, crosswalks) and turns them into CyberHQ-ready
CSV import files. Customer spreadsheets arrive in inconsistent formats, so the tool needs to
identify what kind of content it's looking at and map it to the right CyberHQ template field
by field, using NLP/LLM assistance to minimise manual work — but a human always reviews the
mapping before any file is written, and content is only ever moved or reformatted, never
changed or invented.

## Current state

A working end-to-end MVP exists: upload a spreadsheet → Claude classifies the sheet and
proposes a column mapping → a reviewer edits/approves the mapping in a browser UI → the tool
generates the CyberHQ import CSV. This has been built and smoke-tested in a previous session
(Cowork), zipped, and handed off for continued development in Claude Code.

Stack: Python (FastAPI backend, pandas/openpyxl for spreadsheet parsing, Anthropic SDK for
classification), vanilla HTML/JS/CSS frontend (no build step), no database — everything is a
local, single-user tool for now.

### Repo layout

```
backend/app/
  main.py               FastAPI app + routes (upload, classify, export)
  template_registry.py  Scans templates/, builds the target schema for each template
  ingestion.py          Reads uploaded csv/xlsx into DataFrames
  state.py              In-memory session store
  services/
    classifier.py        Calls Claude to classify a sheet + propose column mapping
    exporter.py           Builds the final CSV from an approved mapping
templates/               CyberHQ's own CSV/xlsx import templates (source of truth for schema)
frontend/static/         Single-page review UI (index.html, app.js, style.css)
samples/                 Test spreadsheets (currently just one synthetic example)
README.md                Setup/run instructions, architecture notes, known limitations
```

## CyberHQ templates gathered so far

Six template files were supplied, covering five "structural" artefact types (one row per
record) plus one framework-specific assessment:

- `upload_risk_register_template.csv` — Risk ID, Name, Description, Status, Treatment,
  Priority, Inherent/Residual Likelihood & Impact, Financial Impact, Due Date, Owner, Risk
  Categories, Risk Source.
- `upload_issue_template.csv` — Ref, Source, Status, Title, Description, Type, Likelihood/
  Impact (band + level), dates, Business Systems, repeatable Action fields (Action Name1/2/3…),
  Other Fields (catch-all), Data Breach, System Misuse.
- `upload_entity_template.csv` — vendor/third-party register: Name, Location, Description,
  Industry, contact details, Priority, Type (Internal/External), employee-count band, data
  accessed, network/physical access, Jurisdiction, Tags. No catch-all field.
- `upload_kbs_template.csv` — Key Business Systems: Name, Description, Business owner, Status,
  Location, Data Stored, Key processes supported, Applicable regulations, Key (Y/N), Master
  Control Framework (crosswalk codes, e.g. `AU-2(0)-5`).
- `upload_resource_template.xlsx` — control/resource library: Resource Name, Description,
  Tags, Type/SubType (Technology, People, Service Provider, Documentation — picklist values
  come from a `Prefill` sheet in the workbook), costs, Master Control Framework (crosswalk).
- `NIST Maturity Assessment Template.csv` — a fixed, ordered NIST CSF question list (Index,
  Section/Subsection, Question, Current Score, Current Answer, Notes, etc.). **Different shape
  of problem** — see "Open decisions" below. Only one framework supplied so far; more will
  follow and should drop into `templates/` without code changes (see README).

The template registry (`template_registry.py`) parses these dynamically by filename
convention, so new templates or frameworks don't need code changes — see the README's
"Adding a new CyberHQ template or framework" section.

## Design principles (why the pipeline is shaped this way)

- The classifier only ever sees column headers and a few sample values, and only ever
  proposes *where a column should go* — never a value itself. It cannot invent or rewrite data.
- "Format" conversions are a fixed, code-implemented list (trim, date reformatting,
  upper/lowercase) — there is no free-text/LLM-authored transform that runs unreviewed
  against real data.
- Every mapping suggestion carries a confidence score and a plain-English rationale, shown to
  the reviewer.
- Nothing is exported until a human approves the mapping in the UI.
- Every source column must be accounted for before export: mapped to a target field, routed
  to a catch-all field, or explicitly confirmed as dropped. This is enforced server-side (a
  409 with the unresolved column list) — the UI can't route around it.

## What's actually been tested

- Template registry: verified it correctly parses all six supplied templates (structural +
  framework), including reading picklist values from the resource template's `Prefill` sheet.
- Export pipeline: tested end-to-end with a synthetic messy vendor spreadsheet
  (`samples/synthetic_vendor_list.csv`) mapped by hand to the entity template. Confirmed the
  "unresolved column" guard blocks export until a drop is explicitly confirmed, and that the
  output CSV has the exact CyberHQ header row with values carried over untouched.
- Frontend: verified it serves correctly alongside the API (static files + `/api/*` routes
  coexisting on the same FastAPI app).
- **Not yet tested**: live LLM classification. The Anthropic API key supplied during the build
  returned a 401 (invalid) when tested directly against Anthropic's API, independent of the
  app code — so the classifier logic itself is unverified against a real model call. Needs a
  working key before this can be trusted.

## Open decisions before continuing

1. **Working API key.** Get a fresh key from the Anthropic Console, put it in `.env` (see
   `.env.example`), and actually exercise `/api/sessions/{id}/classify` against a real sheet
   before trusting the classifier prompt. The key pasted during the original build session was
   invalid and should be treated as burnt regardless — don't reuse it.
2. **Maturity assessment / crosswalk handling.** The NIST template (and future framework
   templates like it) needs row-level *content matching* — matching a customer's own
   assessment answers to the fixed question list by meaning — not column mapping like the
   other five templates. This has been deliberately left out of the mapping UI rather than
   forced into the wrong shape. Needs its own design pass: probably a separate classification
   flow that matches each customer row to the closest `Index` in the target template, then
   maps score/answer/notes fields across, with confidence and human review same as elsewhere.
3. **Real sample spreadsheets.** None were available during the build — only a synthetic
   example. The classifier prompt and exporter's format-transform list should be treated as a
   first draft until tested against real, messy customer files (multiple sheets, inconsistent
   headers, merged cells, etc. are all likely).

## Known limitations to revisit

- No automated handling for multi-part transforms — e.g. splitting a single "Contact Name"
  column into First/Last Name, or banding an exact employee count into the entity template's
  size ranges (`< 5k`, `5k - 10k`, …). The classifier will flag these as low-confidence/no-match
  rather than guess; currently needs manual correction in the review table.
- Sessions are in-memory only — lost on server restart. Fine for a single migration sitting,
  not durable.
- Single-user, no auth — each person runs their own local instance. Was scoped for wider
  SE/CS team use eventually, so packaging/auth may need revisiting once the core pipeline is
  proven out.

## Suggested next steps

1. Get a working Anthropic API key in and run a real classification pass on the synthetic
   sample, then on any real customer spreadsheet that can be sourced (even one).
2. Stress-test the exporter against messier inputs — multiple sheets in one workbook, blank
   columns, duplicate-ish headers.
3. Scope and prototype the maturity-assessment/crosswalk matching flow separately.
4. Once the five structural templates are solid, revisit packaging for the wider team
   (installer, or a hosted single instance rather than everyone running their own).
