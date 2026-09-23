const state = {
  sessionId: null,
  sheets: [],
  currentSheet: null,
  templates: { structural: [], framework_assessment: [] },
  currentTemplateKey: null,
  mappingRows: [], // {source_column, target_field, confidence, rationale, transform}
  settings: { active_provider: null, providers: [] },
  // field -> { mode: "crosswalk"|"content"|"default", ... } - see buildValueConfirmPlan()
  valueConfirmState: {},
};

const CATCHALL_NAMES = ["other fields", "other field", "notes", "comments"];
const TRANSFORMS = [
  ["none", "As-is"],
  ["trim", "Trim whitespace"],
  ["parse_date_ddmmyyyy", "Reformat date -> DD/MM/YYYY"],
  ["parse_date_yyyymmdd", "Reformat date -> YYYY-MM-DD"],
  ["uppercase", "UPPERCASE"],
  ["lowercase", "lowercase"],
];

const $ = (id) => document.getElementById(id);

function banner(el, kind, text) {
  el.innerHTML = `<div class="banner ${kind}">${text}</div>`;
}

async function init() {
  const res = await fetch("/api/templates");
  state.templates = await res.json();
  $("upload-btn").addEventListener("click", uploadFile);
  $("confirm-header-btn").addEventListener("click", confirmHeaderRow);
  $("suggest-template-btn").addEventListener("click", suggestTemplate);
  $("map-columns-btn").addEventListener("click", onMapColumns);
  $("change-template-btn").addEventListener("click", onChangeTemplate);
  $("reclassify-btn").addEventListener("click", () => classify(state.currentTemplateKey));
  $("continue-to-values-btn").addEventListener("click", onContinueToValues);
  $("back-to-mapping-btn").addEventListener("click", onBackToMapping);
  $("export-btn").addEventListener("click", () => attemptExport([]));
  $("confirm-drop-btn").addEventListener("click", onConfirmDrop);
  $("cancel-drop-btn").addEventListener("click", () => $("drop-confirm-panel").classList.add("hidden"));

  $("settings-toggle-btn").addEventListener("click", () => toggleSettingsForm());
  $("provider-select").addEventListener("change", onProviderSelectChange);
  $("toggle-key-visibility-btn").addEventListener("click", toggleKeyVisibility);
  $("test-key-btn").addEventListener("click", testApiKey);
  $("save-settings-btn").addEventListener("click", saveSettings);

  await loadSettings();
}

// ---------- Settings (LLM provider + API key) ----------

const PROVIDER_KEY_HINTS = {
  anthropic: "Starts with sk-ant-.",
  gemini: "Starts with AIza.",
};

async function loadSettings() {
  const res = await fetch("/api/settings");
  state.settings = await res.json();
  renderSettingsSummary();

  // First run, nothing configured yet: open the form so it's obvious what to do.
  const anyConfigured = state.settings.providers.some((p) => p.key_set);
  if (!anyConfigured) {
    toggleSettingsForm(true);
  }

  const preferred = state.settings.active_provider || state.settings.providers[0]?.provider;
  $("provider-select").value = preferred;
  onProviderSelectChange();
}

function providerInfo(providerKey) {
  return state.settings.providers.find((p) => p.provider === providerKey);
}

function renderSettingsSummary() {
  const summary = $("settings-summary");
  const active = state.settings.active_provider;
  const info = active ? providerInfo(active) : null;

  if (!info || !info.key_set) {
    summary.innerHTML = `<div class="settings-summary-line"><span class="pill warn">Not configured</span> Add an API key below to enable automatic classification.</div>`;
    return;
  }

  summary.innerHTML = `
    <div class="settings-summary-line">
      <span class="pill ok">Configured</span>
      <span class="provider-label">${info.label}</span>
      <span class="masked-key">${info.key_masked}</span>
    </div>`;
}

function toggleSettingsForm(forceOpen) {
  const wrap = $("settings-form-wrap");
  const open = forceOpen === true || (forceOpen === undefined && wrap.classList.contains("hidden"));
  wrap.classList.toggle("hidden", !open);
  $("settings-toggle-btn").textContent = open ? "Hide" : "Change";
  if (open) {
    $("api-key-input").value = "";
    $("settings-status").innerHTML = "";
  }
}

function onProviderSelectChange() {
  const providerKey = $("provider-select").value;
  const info = providerInfo(providerKey);
  $("api-key-input").value = "";
  $("api-key-input").placeholder = info?.key_set
    ? `Currently saved: ${info.key_masked} — leave blank to keep it`
    : "Paste your API key";

  const hintParts = [PROVIDER_KEY_HINTS[providerKey] || ""];
  if (info?.console_url) {
    hintParts.push(`<a href="${info.console_url}" target="_blank" rel="noopener">Get a key</a>`);
  }
  $("key-hint").innerHTML = hintParts.filter(Boolean).join(" · ");
}

function toggleKeyVisibility() {
  const input = $("api-key-input");
  const btn = $("toggle-key-visibility-btn");
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  btn.textContent = showing ? "Show" : "Hide";
}

function currentSettingsPayload() {
  return {
    provider: $("provider-select").value,
    api_key: $("api-key-input").value.trim() || undefined,
  };
}

async function testApiKey() {
  const provider = $("provider-select").value;
  const apiKey = $("api-key-input").value.trim();
  if (!apiKey) {
    banner($("settings-status"), "error", "Paste a key first — there's nothing new to test.");
    return;
  }
  banner($("settings-status"), "info", "Testing connection…");
  const res = await fetch("/api/settings/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    banner($("settings-status"), "error", `${err.detail || res.statusText}`);
    return;
  }
  banner($("settings-status"), "ok", "Key works.");
}

async function saveSettings() {
  const payload = currentSettingsPayload();
  banner($("settings-status"), "info", "Saving…");
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    banner($("settings-status"), "error", `${err.detail || res.statusText}`);
    return;
  }
  state.settings = await res.json();
  renderSettingsSummary();
  banner($("settings-status"), "ok", "Saved.");
  toggleSettingsForm(false);
}

async function uploadFile() {
  const input = $("file-input");
  if (!input.files.length) {
    banner($("upload-status"), "error", "Choose a file first.");
    return;
  }
  const fd = new FormData();
  fd.append("file", input.files[0]);
  banner($("upload-status"), "info", "Uploading and parsing…");

  const res = await fetch("/api/upload", { method: "POST", body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    banner($("upload-status"), "error", `Upload failed: ${err.detail || res.statusText}`);
    return;
  }
  const data = await res.json();
  state.sessionId = data.session_id;
  state.sheets = data.sheets;
  banner($("upload-status"), "ok", `Loaded ${data.sheets.length} sheet(s) from ${data.source_filename}.`);
  renderSheetPicker();
}

function renderSheetPicker() {
  const panel = $("sheet-panel");
  panel.classList.remove("hidden");
  const picker = $("sheet-picker");
  picker.innerHTML = "";
  state.sheets.forEach((sheet) => {
    const btn = document.createElement("button");
    btn.textContent = `${sheet.name} (${sheet.row_count} rows, ${sheet.col_count} cols)`;
    btn.addEventListener("click", () => selectSheet(sheet.name));
    picker.appendChild(btn);
  });
  if (state.sheets.length === 1) selectSheet(state.sheets[0].name);
}

function selectSheet(name) {
  state.currentSheet = state.sheets.find((s) => s.name === name);
  document.querySelectorAll("#sheet-picker button").forEach((b) => {
    b.classList.toggle("active", b.textContent.startsWith(name));
  });
  $("template-panel").classList.add("hidden");
  $("mapping-panel").classList.add("hidden");
  $("value-confirm-panel").classList.add("hidden");
  $("drop-confirm-panel").classList.add("hidden");
  $("template-suggestion-banner").innerHTML = "";
  $("header-row-status").innerHTML = "";
  $("header-row-panel").classList.remove("hidden");
  renderHeaderRowPreview();
}

function renderHeaderRowPreview() {
  const sheet = state.currentSheet;
  const wrap = $("header-row-preview");
  const table = document.createElement("table");
  const tbody = document.createElement("tbody");

  sheet.preview_rows.forEach((cells, idx) => {
    const rowNumber = idx + 1;
    const tr = document.createElement("tr");
    tr.dataset.row = String(rowNumber);
    if (rowNumber === sheet.guessed_header_row) tr.classList.add("selected");

    const radioTd = document.createElement("td");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "header-row";
    radio.value = String(rowNumber);
    radio.checked = rowNumber === sheet.guessed_header_row;
    radioTd.appendChild(radio);

    const numTd = document.createElement("td");
    numTd.className = "row-number";
    numTd.textContent = rowNumber;
    if (rowNumber === sheet.guessed_header_row) {
      const badge = document.createElement("span");
      badge.className = "guessed-badge";
      badge.textContent = "guessed";
      numTd.appendChild(badge);
    }

    const cellsTd = document.createElement("td");
    cellsTd.className = "row-cells";
    cellsTd.textContent = cells.filter((c) => c !== "").join(" | ") || "(blank row)";

    tr.append(radioTd, numTd, cellsTd);
    tr.addEventListener("click", () => {
      radio.checked = true;
      wrap.querySelectorAll("tr").forEach((r) => r.classList.remove("selected"));
      tr.classList.add("selected");
    });
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  wrap.innerHTML = "";
  wrap.appendChild(table);
}

async function confirmHeaderRow() {
  const checked = document.querySelector('input[name="header-row"]:checked');
  if (!checked) {
    banner($("header-row-status"), "error", "Pick a row first.");
    return;
  }
  const headerRow = parseInt(checked.value, 10);

  banner($("header-row-status"), "info", "Reading columns…");
  const res = await fetch(`/api/sessions/${state.sessionId}/set-header`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sheet_name: state.currentSheet.name, header_row: headerRow }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    banner($("header-row-status"), "error", `${err.detail || res.statusText}`);
    return;
  }

  const result = await res.json();
  Object.assign(state.currentSheet, result); // adds columns + sample_rows
  banner($("header-row-status"), "ok", `Using row ${headerRow} as the header — found ${result.columns.length} column(s), ${result.row_count} data row(s).`);

  $("mapping-panel").classList.add("hidden");
  $("value-confirm-panel").classList.add("hidden");
  $("template-suggestion-banner").innerHTML = "";
  $("template-panel").classList.remove("hidden");
  populateTemplateSelect();
}

function populateTemplateSelect() {
  const sel = $("template-select");
  sel.innerHTML = "";
  state.templates.structural.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t.key;
    opt.textContent = `${t.label} (${t.source_file})`;
    sel.appendChild(opt);
  });
}

async function suggestTemplate() {
  banner($("template-suggestion-banner"), "info", "Asking the LLM which template looks like the best fit…");
  const res = await fetch(`/api/sessions/${state.sessionId}/suggest-template`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sheet_name: state.currentSheet.name }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    banner($("template-suggestion-banner"), "error", `Suggestion failed: ${err.detail || res.statusText}. Pick a template manually above.`);
    return;
  }

  const result = await res.json();
  if (result.target_template?.key) {
    $("template-select").value = result.target_template.key;
  }
  const pct = Math.round((result.template_confidence || 0) * 100);
  banner(
    $("template-suggestion-banner"),
    pct >= 70 ? "ok" : "warn",
    `Suggested: <strong>${result.target_template?.label || result.best_template}</strong> (${pct}% confidence). ${result.template_rationale || ""}`
  );
}

function onMapColumns() {
  state.currentTemplateKey = $("template-select").value;
  $("mapping-panel").classList.remove("hidden");
  $("value-confirm-panel").classList.add("hidden");
  $("drop-confirm-panel").classList.add("hidden");
  $("mapping-target-label").textContent = currentTemplate()?.label || state.currentTemplateKey;
  classify(state.currentTemplateKey);
  $("mapping-panel").scrollIntoView({ behavior: "smooth" });
}

function onChangeTemplate() {
  $("mapping-panel").classList.add("hidden");
  $("value-confirm-panel").classList.add("hidden");
  $("drop-confirm-panel").classList.add("hidden");
  $("template-panel").scrollIntoView({ behavior: "smooth" });
}

function onBackToMapping() {
  $("value-confirm-panel").classList.add("hidden");
  $("mapping-panel").scrollIntoView({ behavior: "smooth" });
}

async function classify(templateKey) {
  $("value-confirm-panel").classList.add("hidden");
  banner($("mapping-banner"), "info", "Asking the LLM to propose a column mapping…");

  const res = await fetch(`/api/sessions/${state.sessionId}/classify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sheet_name: state.currentSheet.name, template_key: templateKey }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const msg = (err.detail || res.statusText || "").toString();
    banner(
      $("mapping-banner"),
      "error",
      `Automatic mapping failed (${msg}). You can still map columns manually below.`
    );
    // Fall back to an empty/manual mapping against the chosen template.
    state.mappingRows = state.currentSheet.columns.map((c) => ({
      source_column: c,
      target_field: null,
      confidence: 0,
      rationale: "Automatic suggestion unavailable — please map manually.",
      transform: "none",
    }));
    renderMappingTable();
    return;
  }

  const result = await res.json();
  state.mappingRows = result.column_mappings.map((m) => ({ ...m, transform: "none" }));
  banner($("mapping-banner"), "ok", `Proposed a mapping for ${state.mappingRows.length} column(s) — review and adjust below.`);
  renderMappingTable();
}

function currentTemplate() {
  return state.templates.structural.find((t) => t.key === state.currentTemplateKey);
}

function confidenceClass(c) {
  if (c >= 0.75) return "high";
  if (c >= 0.4) return "medium";
  return "low";
}

function renderMappingTable() {
  const tmpl = currentTemplate();
  const tbody = $("mapping-tbody");
  tbody.innerHTML = "";

  state.mappingRows.forEach((row, idx) => {
    const tr = document.createElement("tr");

    const samples = state.currentSheet.sample_rows
      .slice(0, 3)
      .map((r) => r[row.source_column])
      .filter((v) => v !== undefined && v !== "")
      .join(" | ");

    const fieldSelect = document.createElement("select");
    const noneOpt = document.createElement("option");
    noneOpt.value = "";
    noneOpt.textContent = "— unmapped —";
    fieldSelect.appendChild(noneOpt);
    tmpl.columns.forEach((col) => {
      const opt = document.createElement("option");
      opt.value = col;
      opt.textContent = col;
      if (row.target_field === col) opt.selected = true;
      fieldSelect.appendChild(opt);
    });
    fieldSelect.addEventListener("change", () => {
      state.mappingRows[idx].target_field = fieldSelect.value || null;
    });

    const transformSelect = document.createElement("select");
    TRANSFORMS.forEach(([val, label]) => {
      const opt = document.createElement("option");
      opt.value = val;
      opt.textContent = label;
      if (row.transform === val) opt.selected = true;
      transformSelect.appendChild(opt);
    });
    transformSelect.addEventListener("change", () => {
      state.mappingRows[idx].transform = transformSelect.value;
    });

    tr.innerHTML = `
      <td><strong>${row.source_column}</strong></td>
      <td class="sample-values">${samples || "<em>empty</em>"}</td>
      <td></td>
      <td></td>
      <td>
        <span class="confidence ${confidenceClass(row.confidence || 0)}">${Math.round((row.confidence || 0) * 100)}%</span>
        <div class="rationale">${row.rationale || ""}</div>
      </td>
    `;
    tr.children[2].appendChild(fieldSelect);
    tr.children[3].appendChild(transformSelect);
    tbody.appendChild(tr);
  });

  populateCatchAllSelect(tmpl);
}

function populateCatchAllSelect(tmpl) {
  const sel = $("catchall-select");
  sel.innerHTML = '<option value="">— drop unmapped columns instead (you\'ll be asked to confirm) —</option>';
  tmpl.columns.forEach((col) => {
    const opt = document.createElement("option");
    opt.value = col;
    opt.textContent = col;
    sel.appendChild(opt);
  });
  const guess = tmpl.columns.find((c) => CATCHALL_NAMES.includes(c.trim().toLowerCase()));
  if (guess) sel.value = guess;
}

// ---------- Step 6: constrained-field value confirmation (issue #9) ----------

function onContinueToValues() {
  state.valueConfirmState = buildValueConfirmPlan();
  $("value-confirm-panel").classList.remove("hidden");
  $("drop-confirm-panel").classList.add("hidden");
  renderValueConfirmPanel();
  $("value-confirm-panel").scrollIntoView({ behavior: "smooth" });
}

function buildValueConfirmPlan() {
  const tmpl = currentTemplate();
  const allowedFields = Object.keys(tmpl.allowed_values || {}).filter(
    (f) => (tmpl.allowed_values[f] || []).length > 0
  );
  const plan = {};
  allowedFields.forEach((field) => {
    const mappedRow = state.mappingRows.find((r) => r.target_field === field);
    if (mappedRow) {
      plan[field] = { mode: "crosswalk", sourceColumn: mappedRow.source_column, matches: null, selections: {} };
    } else if ((tmpl.content_recommend_fields || []).includes(field)) {
      plan[field] = { mode: "content", recommendations: null, rowValues: null, showAll: false };
    } else {
      plan[field] = { mode: "default", defaultValue: "" };
    }
  });
  return plan;
}

function renderValueConfirmPanel() {
  const tmpl = currentTemplate();
  const container = $("value-confirm-fields");
  container.innerHTML = "";
  Object.entries(state.valueConfirmState).forEach(([field, fieldState]) => {
    const card = document.createElement("div");
    card.className = "value-field-card";
    if (fieldState.mode === "crosswalk") renderCrosswalkCard(card, field, fieldState, tmpl);
    else if (fieldState.mode === "content") renderContentCard(card, field, fieldState, tmpl);
    else renderDefaultCard(card, field, fieldState, tmpl);
    container.appendChild(card);
  });
}

function buildAllowedValueSelect(field, tmpl, currentValue, placeholder, onChange) {
  const select = document.createElement("select");
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = placeholder;
  select.appendChild(noneOpt);
  (tmpl.allowed_values[field] || []).forEach((val) => {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = val;
    select.appendChild(opt);
  });
  select.value = currentValue || "";
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function renderCrosswalkCard(card, field, fieldState, tmpl) {
  card.innerHTML = `<h3>${field} <span class="field-source-note">(from "${fieldState.sourceColumn}")</span></h3>`;

  if (fieldState.error) {
    const p = document.createElement("p");
    p.className = "warn-note";
    p.textContent = fieldState.error;
    card.appendChild(p);
    return;
  }
  if (!fieldState.matches) {
    if (!fieldState.fetching) fetchCrosswalk(field);
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "Fetching suggestions…";
    card.appendChild(p);
    return;
  }
  if (fieldState.matches.length === 0) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "No values found in this column.";
    card.appendChild(p);
    return;
  }

  const table = document.createElement("table");
  table.className = "crosswalk-table";
  table.innerHTML = "<thead><tr><th>Source value</th><th>Maps to</th><th>Confidence &amp; why</th></tr></thead>";
  const tbody = document.createElement("tbody");
  fieldState.matches.forEach((m) => {
    if (fieldState.selections[m.source_value] === undefined) {
      fieldState.selections[m.source_value] = m.suggested_target || "";
    }
    const tr = document.createElement("tr");
    const select = buildAllowedValueSelect(field, tmpl, fieldState.selections[m.source_value], "— leave as-is —", (v) => {
      fieldState.selections[m.source_value] = v;
    });
    tr.innerHTML = `
      <td class="crosswalk-source">${m.source_value}</td>
      <td></td>
      <td>
        <span class="confidence ${confidenceClass(m.confidence || 0)}">${Math.round((m.confidence || 0) * 100)}%</span>
        <div class="rationale">${m.rationale || ""}</div>
      </td>`;
    tr.children[1].appendChild(select);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  card.appendChild(table);
}

async function fetchCrosswalk(field) {
  const fieldState = state.valueConfirmState[field];
  fieldState.fetching = true;
  const res = await fetch(`/api/sessions/${state.sessionId}/crosswalk-values`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sheet_name: state.currentSheet.name,
      source_column: fieldState.sourceColumn,
      target_field: field,
      template_key: state.currentTemplateKey,
    }),
  });
  fieldState.fetching = false;

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    fieldState.error = `Suggestions failed: ${err.detail || res.statusText}. You can still pick values manually below once mapped.`;
    fieldState.matches = [];
    renderValueConfirmPanel();
    return;
  }
  const result = await res.json();
  fieldState.matches = result.matches;
  renderValueConfirmPanel();
}

function renderContentCard(card, field, fieldState, tmpl) {
  card.innerHTML = `<h3>${field} <span class="unmapped-badge">not mapped</span></h3>
    <p class="hint">No source column maps to this field — recommend a value per row from its content instead.</p>`;

  if (!fieldState.recommendations) {
    const btn = document.createElement("button");
    btn.className = "secondary";
    btn.type = "button";
    btn.textContent = fieldState.fetching ? "Recommending…" : "Recommend from content";
    btn.disabled = !!fieldState.fetching;
    btn.addEventListener("click", () => fetchContentRecommendation(field));
    card.appendChild(btn);
    if (fieldState.error) {
      const p = document.createElement("p");
      p.className = "warn-note";
      p.textContent = fieldState.error;
      card.appendChild(p);
    }
    return;
  }

  const indexed = fieldState.recommendations
    .map((r, idx) => (r ? { ...r, idx } : null))
    .filter(Boolean);
  const lowConfidence = indexed.filter((r) => (r.confidence || 0) < 0.4);
  const total = indexed.length;

  const summary = document.createElement("div");
  summary.className = "recommend-summary";
  summary.textContent = `${total} row(s) classified${lowConfidence.length ? `, ${lowConfidence.length} low-confidence` : ""}. `;
  if (total) {
    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "recommend-toggle";
    toggleBtn.textContent = fieldState.showAll ? "Show only low-confidence" : `Show all ${total} row(s)`;
    toggleBtn.addEventListener("click", () => {
      fieldState.showAll = !fieldState.showAll;
      renderValueConfirmPanel();
    });
    summary.appendChild(toggleBtn);
  }
  card.appendChild(summary);

  const rowsToShow = fieldState.showAll ? indexed : lowConfidence;
  if (rowsToShow.length) {
    const table = document.createElement("table");
    table.className = "crosswalk-table";
    table.innerHTML = "<thead><tr><th>Row content</th><th>Recommended</th><th>Confidence &amp; why</th></tr></thead>";
    const tbody = document.createElement("tbody");
    rowsToShow.forEach((r) => {
      const tr = document.createElement("tr");
      const select = buildAllowedValueSelect(field, tmpl, fieldState.rowValues[r.idx], "— leave blank —", (v) => {
        fieldState.rowValues[r.idx] = v;
      });
      tr.innerHTML = `
        <td class="sample-values">${r.content_preview || ""}</td>
        <td></td>
        <td>
          <span class="confidence ${confidenceClass(r.confidence || 0)}">${Math.round((r.confidence || 0) * 100)}%</span>
          <div class="rationale">${r.rationale || ""}</div>
        </td>`;
      tr.children[1].appendChild(select);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    card.appendChild(table);
  }
}

async function fetchContentRecommendation(field) {
  const fieldState = state.valueConfirmState[field];
  fieldState.fetching = true;
  renderValueConfirmPanel();

  const res = await fetch(`/api/sessions/${state.sessionId}/recommend-from-content`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sheet_name: state.currentSheet.name,
      target_field: field,
      template_key: state.currentTemplateKey,
      mapping: state.mappingRows.map((r) => ({
        source_column: r.source_column,
        target_field: r.target_field,
        transform: r.transform || "none",
      })),
    }),
  });
  fieldState.fetching = false;

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    fieldState.error = err.detail || res.statusText;
    renderValueConfirmPanel();
    return;
  }
  const result = await res.json();
  fieldState.recommendations = result.recommendations;
  fieldState.rowValues = result.recommendations.map((r) => (r ? r.suggested_target || "" : ""));
  renderValueConfirmPanel();
}

function renderDefaultCard(card, field, fieldState, tmpl) {
  card.innerHTML = `<h3>${field} <span class="unmapped-badge">not mapped</span></h3>`;
  const label = document.createElement("label");
  label.className = "hint";
  label.style.display = "block";
  label.style.marginBottom = ".25rem";
  label.textContent = "Set a default for all rows (optional)";
  card.appendChild(label);

  const select = buildAllowedValueSelect(field, tmpl, fieldState.defaultValue, "— leave blank —", (v) => {
    fieldState.defaultValue = v;
  });
  card.appendChild(select);

  const isNumericRange = (tmpl.allowed_values[field] || []).every((v) => /^\d+$/.test(v));
  if (isNumericRange) {
    const warn = document.createElement("p");
    warn.className = "warn-note";
    warn.textContent =
      "Heads up: this writes the same number into every row for this field — usually only appropriate if every row genuinely shares that rating.";
    card.appendChild(warn);
  }
}

function buildValuePayload() {
  const valueMapsBySourceColumn = {};
  const fieldDefaults = {};
  const fieldRowValues = {};

  Object.entries(state.valueConfirmState).forEach(([field, fs]) => {
    if (fs.mode === "crosswalk" && fs.matches) {
      const map = {};
      Object.entries(fs.selections).forEach(([sourceValue, target]) => {
        if (target) map[sourceValue] = target;
      });
      if (Object.keys(map).length) valueMapsBySourceColumn[fs.sourceColumn] = map;
    } else if (fs.mode === "content" && fs.rowValues) {
      fieldRowValues[field] = fs.rowValues.map((v) => v || "");
    } else if (fs.mode === "default" && fs.defaultValue) {
      fieldDefaults[field] = fs.defaultValue;
    }
  });

  return { valueMapsBySourceColumn, fieldDefaults, fieldRowValues };
}

async function attemptExport(confirmedDropColumns) {
  const catchAll = $("catchall-select").value || null;
  const { valueMapsBySourceColumn, fieldDefaults, fieldRowValues } = buildValuePayload();
  const payload = {
    sheet_name: state.currentSheet.name,
    template_key: state.currentTemplateKey,
    mapping: state.mappingRows.map((r) => ({
      source_column: r.source_column,
      target_field: r.target_field,
      transform: r.transform || "none",
      value_map: valueMapsBySourceColumn[r.source_column] || undefined,
    })),
    catch_all_field: catchAll,
    confirmed_drop_columns: confirmedDropColumns,
    field_defaults: Object.keys(fieldDefaults).length ? fieldDefaults : undefined,
    field_row_values: Object.keys(fieldRowValues).length ? fieldRowValues : undefined,
  };

  banner($("export-status"), "info", "Generating CSV…");
  const res = await fetch(`/api/sessions/${state.sessionId}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.status === 409) {
    const err = await res.json();
    const cols = err.detail?.columns || [];
    $("drop-list").innerHTML = cols.map((c) => `<li>${c}</li>`).join("");
    $("drop-confirm-panel").classList.remove("hidden");
    $("export-status").innerHTML = "";
    $("drop-confirm-panel").scrollIntoView({ behavior: "smooth" });
    return;
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    banner($("export-status"), "error", `Export failed: ${JSON.stringify(err.detail || res.statusText)}`);
    return;
  }

  const warnings = res.headers.get("X-Export-Warnings");
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : "cyberhq_import.csv";

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);

  $("drop-confirm-panel").classList.add("hidden");
  banner(
    $("export-status"),
    "ok",
    `Downloaded ${filename}.${warnings && warnings !== "0" ? ` (${warnings} note(s) — check field consolidation.)` : ""}`
  );
}

function onConfirmDrop() {
  const cols = Array.from($("drop-list").children).map((li) => li.textContent);
  attemptExport(cols);
}

init();
