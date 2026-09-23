# CyberHQ GRC Data Migration Tool

Turns a customer's existing GRC spreadsheets (risk registers, issue logs, vendor/third-party
lists, key business systems, control/resource libraries) into CyberHQ-ready import CSVs -
with a human reviewing and approving the field mapping before anything is written.

## How it works

1. **Configure** - pick an LLM provider (Claude or Gemini) and add an API key on the
   Settings panel at the top of the page. See "LLM provider settings" below.
2. **Upload** a customer spreadsheet (.csv or .xlsx, any sheet layout).
3. **Classify** - the configured LLM reads the column headers and a few sample rows and
   proposes which CyberHQ template this sheet is (risk register, issue register,
   entity/vendor list, key business systems, or resource/control library) and how each
   source column maps to a target field, with a confidence score and a short rationale per
   column.
4. **Review** - every suggestion is editable. Change the target template, remap any column,
   choose a format conversion (date format, list delimiter, case), route leftover columns to
   a catch-all field, or leave columns unmapped.
5. **Export** - once you're happy, generate the CSV. If any source column is left unmapped
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

Requires Python 3.11+ (a version with a prebuilt `pydantic-core` wheel available - as of
writing that means 3.11-3.13; 3.14 will fail to build `pydantic-core` from source).

```bash
cd grc-migration-tool
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

You don't need to touch `.env` by hand - the app creates and manages it for you from the
Settings panel once it's running (see below). `.env.example` documents the variable names it
uses, for reference only.

## Running it

```bash
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000 in a browser.

## LLM provider settings

The "LLM provider settings" panel at the top of the page lets each user bring their own key
for either supported provider - nobody needs a shared or pre-provisioned key to use the tool:

- **Claude (Anthropic)** - get a key from the
  [Anthropic Console](https://console.anthropic.com/settings/keys).
- **Gemini (Google)** - get a key from [Google AI Studio](https://aistudio.google.com/apikey).

Pick a provider, paste a key, optionally hit **Test connection** (a cheap, non-generating
call that just checks the key works), then **Save**. Both `classify_sheet` implementations
(`backend/app/services/providers/anthropic_provider.py` and `.../gemini_provider.py`) are
held to the exact same prompt and output schema (`.../shared.py`), so switching providers
doesn't change the classification behaviour or the data-integrity rules below.

Security practices this follows, given this is a local single-user tool with no other secret
store available:

- A key is validated (a real API call) **before** it's saved, so a typo or a revoked key
  can't silently get persisted.
- Keys are written only to `.env`, which is already git-ignored (see `.gitignore`) - a saved
  key can never end up committed.
- `.env` is written with owner-only file permissions (`chmod 600`) every time it's updated.
- A key is never sent back to the browser in full once saved - the Settings panel only ever
  shows a masked form (e.g. `sk-a********-...`), and the input field is cleared after every
  save so nothing lingers in the page.
- Treat a key like a password regardless: never paste one into Slack, email, or a chat tool.
  If one ever ends up somewhere it shouldn't, rotate it (revoke + generate a new one) in that
  provider's console straight away.

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

- **Gemini model choice matters more than expected.** With a real key, the full `-flash` and
  `-pro` tiers (`gemini-flash-latest`, `gemini-pro-latest`, etc.) either 503'd
  ("high demand") on our actual prompt size (~9k characters, once all five template
  definitions are included) or hit a billing/quota wall - reproducible with generic filler
  text of the same length, so it wasn't specific to our prompt content or schema. The
  `-flash-lite` tier (default: `gemini-flash-lite-latest`) handled the same prompt reliably
  and produced correctly-shaped, sensible mappings. If classification is failing on Gemini,
  try a `-flash-lite` model via `GEMINI_MODEL` in Settings before assuming the prompt/schema
  is broken. Claude hasn't yet been verified with a real key - see the open decisions list in
  `PROJECT_BRIEF.md`.
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
  main.py               FastAPI app + routes (settings, upload, classify, export)
  settings.py           Reads/writes .env for LLM provider + API key (see "LLM provider settings")
  template_registry.py  Scans templates/, builds the target schema for each one
  ingestion.py           Reads uploaded csv/xlsx into DataFrames
  state.py               In-memory session store
  services/
    classifier.py         Picks the configured provider and asks it to classify + propose a mapping
    exporter.py            Builds the final CSV from an approved mapping
    providers/
      shared.py             Provider-agnostic prompt + output schema (the actual data-integrity rules)
      anthropic_provider.py Claude backend
      gemini_provider.py    Gemini backend
templates/               CyberHQ's own CSV/xlsx import templates (source of truth)
frontend/static/         Single-page vanilla JS/HTML/CSS review UI (includes Settings panel)
samples/                 Test spreadsheets (synthetic, since no real ones yet)
```
