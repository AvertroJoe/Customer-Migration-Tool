const state = {
  sessionId: null,
  sheets: [],
  currentSheet: null,
  templates: { structural: [], framework_assessment: [] },
  currentTemplateKey: null,
  mappingRows: [], // {source_column, target_field, confidence, rationale, transform}
  settings: { active_provider: null, providers: [] },
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
  $("suggest-template-btn").addEventListener("click", suggestTemplate);
  $("map-columns-btn").addEventListener("click", onMapColumns);
  $("change-template-btn").addEventListener("click", onChangeTemplate);
  $("reclassify-btn").addEventListener("click", () => classify(state.currentTemplateKey));
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
    btn.textContent = `${sheet.name} (${sheet.row_count} rows, ${sheet.columns.length} cols)`;
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
  $("mapping-panel").classList.add("hidden");
  $("drop-confirm-panel").classList.add("hidden");
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
  $("drop-confirm-panel").classList.add("hidden");
  $("mapping-target-label").textContent = currentTemplate()?.label || state.currentTemplateKey;
  classify(state.currentTemplateKey);
  $("mapping-panel").scrollIntoView({ behavior: "smooth" });
}

function onChangeTemplate() {
  $("mapping-panel").classList.add("hidden");
  $("drop-confirm-panel").classList.add("hidden");
  $("template-panel").scrollIntoView({ behavior: "smooth" });
}

async function classify(templateKey) {
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

async function attemptExport(confirmedDropColumns) {
  const catchAll = $("catchall-select").value || null;
  const payload = {
    sheet_name: state.currentSheet.name,
    template_key: state.currentTemplateKey,
    mapping: state.mappingRows.map((r) => ({
      source_column: r.source_column,
      target_field: r.target_field,
      transform: r.transform || "none",
    })),
    catch_all_field: catchAll,
    confirmed_drop_columns: confirmedDropColumns,
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
