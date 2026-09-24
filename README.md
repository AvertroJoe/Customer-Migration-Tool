# CyberHQ GRC Data Migration Tool

Turns a customer's existing GRC spreadsheets (risk registers, issue logs, vendor/third-party
lists, key business systems, control/resource libraries) into CyberHQ-ready import CSVs -
with a human reviewing and approving the field mapping before anything is written.

## Getting started

No install, no Python, no git required - download one small file and run it:

1. Go to the [latest release](https://github.com/AvertroJoe/Customer-Migration-Tool/releases/latest)
   and download `run-mac.zip` (macOS) or `run-windows.zip` (Windows).
2. Unzip it, then run the file inside:
   - **macOS**: double-click `run-mac.command`. The first time, macOS will refuse to open it
     since this isn't code-signed - on newer macOS (Sequoia+) the dialog you get ("Apple
     could not verify...") only offers **Done** / **Move to Bin**, no override button at
     all, so the fix is Terminal, not a click-through:
     ```bash
     xattr -d com.apple.quarantine ~/Downloads/run-mac.command
     ```
     (adjust the path if you saved it somewhere other than Downloads - or after typing
     `xattr -d com.apple.quarantine ` with a trailing space, drag the file from Finder into
     the Terminal window to auto-fill the exact path). This only clears the "downloaded from
     the internet" tag on this one file - it doesn't touch any system-wide security setting,
     doesn't disable Gatekeeper in general, and doesn't affect any other file. A fresh
     download (a future release) will need the same one-time fix, since quarantine is
     re-applied automatically to anything newly downloaded. Once cleared, double-click the
     file normally. Two alternatives if you'd rather not use Terminal, though less reliable
     on recent macOS: right-click (Control-click) the file and choose **Open** instead of
     double-clicking, which sometimes shows an actual **Open** button; or **System Settings
     → Privacy & Security**, scroll down to the blocked-file notice, and click **Open
     Anyway**.
   - **Windows**: double-click `run-windows.bat`. Windows SmartScreen (or your antivirus) may
     flag it the first time since it downloads and runs further code - choose **More info →
     Run anyway**. If your organisation's IT locks this down further, they may need to
     allowlist the script.
3. A terminal window opens, sets itself up (first run only - later runs are much faster),
   and your browser opens on the app. Closing that terminal window stops the app.

There's no desktop shortcut - just keep the downloaded file somewhere and run it again next
time. It checks for updates on every launch and asks (a native pop-up, not silent) before
applying one - see `launcher/bootstrap.py` if you want the details.

## How it works

1. **Configure** - pick an LLM provider (Claude or Gemini) and add an API key on the
   Settings panel at the top of the page. See "LLM provider settings" below.
2. **Upload** a customer spreadsheet (.csv or .xlsx, any sheet layout) and pick the sheet to
   migrate.
3. **Confirm the header row** - see "Header row detection" below. Real files often have a
   title or notes row above the real headers; the tool guesses where the real header starts
   and the reviewer confirms (or picks a different row) before anything else happens.
4. **Choose the target template** - risk register, issue register, entity/vendor list, key
   business systems, or resource/control library. The reviewer picks this, since they
   already know what the file is; not sure? "Suggest a template" asks the LLM to guess from
   the column headers and sample rows, as an optional aid rather than the default path.
5. **Map** - once the target template is set, the configured LLM proposes how each source
   column maps to a target field, with a confidence score and a short rationale per column.
   Every suggestion is editable: remap any column, choose a format conversion (date format,
   list delimiter, case), route leftover columns to a catch-all field, or leave columns
   unmapped.
6. **Confirm constrained-field values** - see "Constrained-field value mapping" below. Some
   CyberHQ fields (Status, Priority, ...) only accept a fixed set of values; this step
   translates what's actually in the source data into one of those, with the reviewer
   confirming every value.
7. **Export** - once you're happy, generate the CSV. If any source column is left unmapped
   with nowhere to go, the tool stops and makes you explicitly confirm it should be dropped -
   it never discards data silently.

## Header row detection

Real customer spreadsheets often aren't headers-first - a common pattern is a free-text
title or instructions row (sometimes two, plus a blank spacer) before the real column
headers start. Assuming row 1 is always the header would silently corrupt every mapping on
files shaped like that.

Instead, `backend/app/ingestion.py`'s `guess_header_row()` scans the first 20 rows of the raw
sheet and guesses which one is the real header: a title row is typically narrow (one or two
populated cells, the rest blank), while the real header row is close to the sheet's full
width and is followed by more rows of similar width. That guess is never applied silently -
the reviewer sees a preview of the top of the sheet with the guessed row highlighted, and
either confirms it or clicks a different row (`POST /api/sessions/{id}/set-header`). This
follows the same human-confirms-the-guess pattern as template selection, for the same
reason: a wrong guess here would be a wrong guess in every single row of the export.

## Constrained-field value mapping

Column mapping alone isn't enough for fields like `Status` or `Priority` - CyberHQ only
accepts a fixed set of values for these, and a source column's actual values (e.g. a
customer's own `State` column with values like "Review"/"Respond") often don't match that
vocabulary at all. Copying them through unchanged would produce an invalid import. The
allowed values themselves are confirmed, hardcoded data (`template_registry.py`'s
`ALLOWED_VALUES`) - the tool never invents what CyberHQ's picklists are.

Three mechanisms, chosen automatically per field based on whether it's mapped:
- **Mapped to a source column**: every distinct value actually present gets a proposed
  translation to the closest allowed value, with confidence and rationale
  (`POST /api/sessions/{id}/crosswalk-values`) - the reviewer confirms or changes each one.
  A value with no reasonable match is left as `null` (flagged, not forced) rather than
  guessed.
- **Unmapped, but the field genuinely needs a per-row answer** (currently `Risk Categories`
  and `Issue Type`): a "Recommend from content" action classifies each row individually from
  its own title/description text (`POST /api/sessions/{id}/recommend-from-content`), using
  whichever source columns are already mapped to those descriptive fields.
- **Unmapped, everything else**: the reviewer can set one default value applied to every
  row (never a silent default - always an explicit choice, with an extra warning shown for
  numeric-range fields like Likelihood/Impact, since defaulting those writes the same score
  into every row).

A value that's approved for translation but doesn't match anything is still passed through
unchanged at export time - never silently dropped - and shows up as a warning
(`exporter.py`'s `build_export` return value) so it's visible after export, not just during
review.

## Data integrity, by design

- The classifier only ever sees column headers and a handful of sample values. It proposes
  *where a column should go*, never a value itself - it cannot invent, guess, or rewrite data.
- "Format" conversions (`transform` in the UI) are limited to a fixed, code-implemented list:
  trim whitespace, date reformatting, upper/lowercase. There is no free-text/LLM-authored
  transform that runs unreviewed against your data.
- Nothing is exported until a person has reviewed and approved the mapping in the UI.
- Every source column must be accounted for - mapped, routed to a catch-all field, or
  explicitly confirmed as dropped - before a CSV is produced.

## Developer setup

For working on the code itself, rather than just running it - the "Getting started" section
above is what to point regular users at.

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

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

No API key or network access needed - every LLM call is mocked at the `app.services.classifier`
boundary (see `tests/test_providers.py`/`tests/test_api.py`). Runs the same way in CI
(`.github/workflows/tests.yml`, on every push/PR to `main`) as it does locally. See
`tests/` for what's covered - unit tests for header-row detection, the exporter's value-mapping
logic (issue #9), and template parsing; mocked provider/API-level tests on top of that.

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

- **`run-windows.bat` / the Windows launcher path is untested on real Windows.** The macOS
  path (`run-mac.command`) has been verified end to end - real double-click, real first
  install (Python + venv + dependencies via `uv`, server start, browser open), real fast-path
  on a second launch. The Windows `.bat` has only been reviewed, not run, since there's no
  Windows machine available here. Get a real test from a Windows user before pointing the
  wider team at it.
- **Gemini model choice matters more than expected.** With a real key, the full `-flash` and
  `-pro` tiers (`gemini-flash-latest`, `gemini-pro-latest`, etc.) either 503'd ("high demand")
  on a ~9k character prompt (the size of the old all-five-templates-at-once classify prompt,
  since replaced - see below - but still the size of the "Suggest a template" prompt, which
  still sends every candidate template) or hit a billing/quota wall - reproducible with
  generic filler text of the same length, so it wasn't specific to our prompt content or
  schema. The `-flash-lite` tier (default: `gemini-flash-lite-latest`) handled the same
  prompt reliably and produced correctly-shaped, sensible mappings. If a Gemini call is
  failing, try a `-flash-lite` model via `GEMINI_MODEL` in Settings before assuming the
  prompt/schema is broken. Claude hasn't yet been verified with a real key - see the open
  decisions list in `PROJECT_BRIEF.md`.
- **Column mapping now requires the reviewer to pick the target template first** (step 4
  above) rather than having the LLM guess it as part of every mapping call. This shrinks the
  mapping prompt to one template's fields instead of all five (directly avoiding the size
  issue above on the default path) and removes template misclassification as a failure mode,
  since the reviewer usually already knows what kind of file they're migrating. The "Suggest
  a template" button is a separate, opt-in call for when they don't - it still sends every
  candidate template, so it inherits the model-choice caveat above.
- **Header row detection is a heuristic, not a guarantee** (see "Header row detection"
  above). It's been verified against a real file with a title row + blank spacer above the
  header (correctly guessed row 3), but hasn't been tried against trickier shapes - multiple
  title rows, merged cells, a sheet where the header itself is sparse (few populated cells).
  The reviewer always confirms the guess before anything downstream happens, so a wrong guess
  is corrected, never silently applied - but it may need more clicks on messier files than
  on this one.
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
- **Only one real customer file tested so far** (the issue register in #3) - the pipeline
  has been verified end-to-end against it and against a synthetic example
  (`samples/synthetic_vendor_list.csv`), but the other four structural templates and
  genuinely messy shapes (multi-sheet, merged cells, duplicate headers) haven't been tried
  against real files yet.
- **Constrained-field value mapping's content-recommendation path has no batching** (see
  "Constrained-field value mapping" above) - one call per sheet, curated to just the
  relevant title/description columns rather than a full row dump, but still unbounded in row
  count. On a sheet with many rows this could reach the same order-of-magnitude prompt size
  that already caused Gemini 503s on full `-flash`/`-pro` tiers - a known risk, not yet hit
  in practice, not yet mitigated with chunking.
- **Entity's `Data Accessed 1/2` and `Stored Data Location 1/2`** don't have confirmed
  allowed values yet, so they get no crosswalk/default treatment - deferred until those
  lists are finalised.
- **Master Control Framework crosswalk fields** (KBS, resource) aren't covered by
  constrained-field value mapping - matching a customer's framework codes to CyberHQ's is a
  different, harder problem (see the maturity-assessment issue above), not a simple picklist.
- Sessions are in-memory only, by design - this is a single-user, single-sitting local tool,
  not a shared/persistent service, so nothing is lost that matters (re-upload if the server
  restarts mid-review). Same reasoning for no auth: each person runs their own local
  instance and brings their own key. If that ever needs to change (a shared/hosted
  instance), that's its own, deliberately separate decision - not something to solve here.

## Project layout

```
backend/app/
  main.py               FastAPI app + routes (settings, upload, classify, export)
  settings.py           Reads/writes .env for LLM provider + API key (see "LLM provider settings")
  template_registry.py  Scans templates/, builds the target schema for each one - also holds
                         ALLOWED_VALUES/DELIMITED_FIELDS/CONTENT_RECOMMEND_FIELDS/CONTENT_FIELDS
  ingestion.py           Reads uploaded csv/xlsx as raw rows, guesses + builds the header row
  state.py               In-memory session store (raw rows until header confirmed, then a DataFrame)
  services/
    classifier.py         Picks the configured provider and asks it to classify/suggest/match/recommend
    exporter.py            Builds the final CSV from an approved mapping + constrained-value choices
    providers/
      shared.py             Provider-agnostic prompts + output schemas (the actual data-integrity rules)
      anthropic_provider.py Claude backend
      gemini_provider.py    Gemini backend
templates/               CyberHQ's own CSV/xlsx import templates (source of truth)
frontend/static/         Single-page vanilla JS/HTML/CSS review UI (includes Settings panel)
samples/                 Test spreadsheets (synthetic, since no real ones yet)
launcher/
  bootstrap.py             Self-updating launcher logic (see "Getting started") - stdlib-only
  run-mac.command           Tiny macOS stub, published as a Release asset
  run-windows.bat            Tiny Windows stub, published as a Release asset
```
