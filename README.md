# CyberHQ GRC Data Migration Tool

Turns a customer's existing GRC spreadsheets (risk registers, issue logs, vendor/third-party
lists, key business systems, control/resource libraries) into CyberHQ-ready import CSVs -
with a human reviewing and approving the field mapping before anything is written.

## How it works

1. **Upload** a customer spreadsheet (.csv or .xlsx, any sheet layout).
2. **Classify** - Claude reads the column headers and a few sample rows and proposes which
   CyberHQ template this sheet is (risk register, issue register, entity/vendor list, key
   business systems, or resource/control library) and how each source column maps to a
   target field, with a confidence score and a short rationale per column.
3. **Review** - every suggestion is editable. Change the target template, remap any column,
   choose a format conversion (date format, list delimiter, case), route leftover columns to
   a catch-all field, or leave columns unmapped.
4. **Export** - once you're happy, generate the CSV. If any source column is left unmapped
   with nowhere to go, the tool stops and makes you explicitly confirm it should be dropped -
   it never discards data silently.

## Data integrity, by design

- The classifier only ever sees column headers and a handful of sample values. It proposes
  *where a column should go*, never a value itself - it cannot invent, guess, or rewrite data.
- "Format" conversions (`transform` in the UI) are limited to a fixed, code-implemented list:
  trim whitespace, date reformatting, upper/lowercase. There is no free-text/LLM-authored
  transform that runs unreviewed against your data.
- Nothing is exported until a person has reviewed and approved the mapping in the UI.
- Every source column must be accounted for - mapped, routed to a catch-all field, or
  explicitly confirmed as dropped - before a CSV is produced.

## Setup

Requires Python 3.11+.

```bash
cd grc-migration-tool
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and add your own Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Get a key from the [Anthropic Console](https://console.anthropic.com/settings/keys). Treat it
like a password - `.env` is git-ignored so it won't get committed, but never paste a live key
into Slack, email, or a chat tool. If a key is ever pasted somewhere it shouldn't be, rotate it
(revoke + generate a new one) in the console straight away.

## Running it

```bash
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000 in a browser.

## Adding a new CyberHQ template or framework

Drop the file straight into `templates/` - no code changes needed, as long as the filename
follows one of these patterns:

- `upload_<type>_template.csv` or `.xlsx` - a structural template (one row per record). The
  tool reads its header row as the target field list automatically. For an `.xlsx` template,
  a sheet named `Prefill`/`Lookup`/`Reference`/`Dropdowns` (if present) is read as allowed
  picklist values per column and shown to the reviewer and the classifier.
- `<Framework Name> Maturity Assessment Template.csv` - a framework-specific fixed question
  list (see "Known limitations" below - these aren't wired into the mapping UI yet).

Optionally add plain-English field notes in `backend/app/template_registry.py` -
`FIELD_NOTES[<key>]` - to help the classifier understand ambiguous columns (e.g. that a field
is semicolon-delimited, or expects a specific date format). This is additive; templates work
fine without it, just with slightly less context for the LLM.

Only the NIST CSF maturity assessment template is in `templates/` today - more frameworks can
be added the same way as they're supplied.

## Known limitations / not yet built

- **Framework maturity assessments** (e.g. the NIST CSF template) are a different shape of
  problem to the other five templates: they're a fixed, ordered question list that a
  customer's own assessment/gap-analysis content needs to be *matched against* by meaning
  (which question does this row answer?), not a simple column-to-column mapping. The registry
  picks these files up and labels them `framework_assessment`, but the classify/mapping UI
  only handles `structural` templates for now. Worth a separate design pass before building -
  flag to Joe before starting.
- **Multi-part transforms** (e.g. splitting a single "Contact Name" column into First/Last
  Name, or banding an exact employee count into a template's size ranges) aren't automated -
  the classifier will flag these as low-confidence/no-match rather than guess, and they need
  manual correction in the review table today. Worth revisiting once we see how often real
  customer data needs this.
- **No sample customer spreadsheets have been tested against this yet** - the pipeline has
  been verified end-to-end with a synthetic example (`samples/synthetic_vendor_list.csv`).
  Real customer files, especially messier or multi-sheet ones, will surface edge cases the
  classifier prompt and exporter don't yet handle gracefully.
- Sessions are held in memory and are lost on server restart - fine for a single migration
  sitting, not a durable store. Re-upload if the server restarts mid-review.
- Single-user local tool as it stands - each person on the team runs their own instance. No
  auth, no multi-user session isolation beyond the session ID.

## Project layout

```
backend/app/
  main.py               FastAPI app + routes (upload, classify, export)
  template_registry.py  Scans templates/, builds the target schema for each one
  ingestion.py           Reads uploaded csv/xlsx into DataFrames
  state.py               In-memory session store
  services/
    classifier.py         Calls Claude to classify + propose column mapping
    exporter.py            Builds the final CSV from an approved mapping
templates/               CyberHQ's own CSV/xlsx import templates (source of truth)
frontend/static/         Single-page vanilla JS/HTML/CSS review UI
samples/                 Test spreadsheets (synthetic, since no real ones yet)
```
