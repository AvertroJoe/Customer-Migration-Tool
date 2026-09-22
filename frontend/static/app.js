const state = {
  sessionId: null,
  sheets: [],
  currentSheet: null,
  templates: { structural: [], framework_assessment: [] },
  currentTemplateKey: null,
  mappingRows: [], // {source_column, target_field, confidence, rationale, transform}
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
  $("template-select").addEventListener("change", onTemplateChange);
  $("reclassify-btn").addEventListener("click", () => classify(state.currentTemplateKey));
  $("export-btn").addEventListener("click", () => attemptExport([]));
  $("confirm-drop-btn").addEventListener("click", onConfirmDrop);
  $("cancel-drop-btn").addEventListener("click", () => $("drop-confirm-panel").classList.add("hidden"));
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
  $("mapping-panel").classList.remove("hidden");
  $("drop-confirm-panel").classList.add("hidden");
  populateTemplateSelect();
  classify(null);
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

async function classify(forceTemplateKey) {
  banner($("mapping-banner"), "info", "Asking Claude to classify this sheet and propose a mapping…");
  const body = { sheet_name: state.currentSheet.name };
  if (forceTemplateKey) body.template_key = forceTemplateKey;

  const res = await fetch(`/api/sessions/${state.sessionId}/classify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const msg = (err.detail || res.statusText || "").toString();
    banner(
      $("mapping-banner"),
      "error",
      `Automatic classification failed (${msg}). You can still map columns manually below using the template dropdown.`
    );
    // Fall back to an empty/manual mapping against whatever template is currently selected.
    const fallbackKey = forceTemplateKey || state.templates.structural[0]?.key;
    state.currentTemplateKey = fallbackKey;
    $("template-select").value = fallbackKey;
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
  state.currentTemplateKey = result.best_template;
  $("template-select").value = result.best_template;
  state.mappingRows = result.column_mappings.map((m) => ({ ...m, transform: "none" }));

  const pct = Math.round((result.template_confidence || 0) * 100);
  banner(
    $("mapping-banner"),
    pct >= 70 ? "ok" : "warn",
    `Best match: <strong>${result.target_template?.label || result.best_template}</strong> (${pct}% confidence). ${result.template_rationale || ""}`
  );
  renderMappingTable();
}

function currentTemplate() {
  return state.templates.structural.find((t) => t.key === state.currentTemplateKey);
}

function onTemplateChange() {
  state.currentTemplateKey = $("template-select").value;
  // Re-map existing source columns against the new template's column list, keep
  // whatever mapping the reviewer already set where the field name still exists,
  // clear it where it no longer applies.
  const tmpl = currentTemplate();
  const validFields = new Set(tmpl.columns);
  state.mappingRows.forEach((row) => {
    if (row.target_field && !validFields.has(row.target_field)) {
      row.target_field = null;
      row.confidence = 0;
      row.rationale = "Cleared — not a field in the newly selected template.";
    }
  });
  renderMappingTable();
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
